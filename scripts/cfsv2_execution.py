"""Bounded CFS decoding with deterministic aggregation and shared request pacing."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import numpy as np


class RequestLimiter:
    def __init__(self, delay, last_request=0):
        self.delay = max(0., delay)
        self.last = last_request
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            deadline = self.last + self.delay
            remaining = deadline - time.monotonic()
            while remaining > 0:
                time.sleep(remaining)
                # Windows timers can wake several milliseconds early. Keep
                # the shared request gate closed until the full delay elapsed.
                remaining = deadline - time.monotonic()
            self.last = time.monotonic()
            return self.last


def validate_retained(grid, spec):
    expected = spec.get('grid_shape')
    if expected and (len(grid.lons), len(grid.lats)) != tuple(expected):
        raise ValueError('Retained CFS grid shape does not match the product')
    if np.shape(grid.values) != (len(grid.lats), len(grid.lons)):
        raise ValueError('Retained CFS grid is incomplete')
    if not np.isfinite(grid.values).all():
        raise ValueError('Retained CFS grid contains missing values')


def parallel_cycles(decode, args, init, target, members, cycles, cache_dir,
                    state_dir, wgrib2, repo_root, last_request, spec, return_members):
    # The existing partial-window path retains its detailed failure semantics.
    workers = min(4, max(1, getattr(args, 'decode_workers', 1)))
    if workers == 1 or len(cycles) < 2 or args.allow_partial_rolling:
        return None
    if len(set(cycles)) != len(cycles):
        raise ValueError('Parallel CFS decoding requires unique cycles')
    child = argparse.Namespace(**vars(args))
    child.decode_workers = 1
    child._request_limiter = RequestLimiter(args.request_delay, last_request)

    def one(cycle):
        return decode(child, init, target, members, [cycle], cache_dir,
                      state_dir, wgrib2, repo_root, last_request, spec, True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cycles))
    # Ordered map keeps the serial summation order and provenance order.
    grids = {cycle: result[6][cycle] for cycle, result in zip(cycles, results)}
    from cfsv2_seasonal import mean_grids
    count = len(cycles)
    result = (mean_grids(list(grids.values())),
              [source for item in results for source in item[1]], count, count,
              f'{count}/{count}-cycle rolling mean', max(item[5] for item in results))
    return (*result, grids) if return_members else result
