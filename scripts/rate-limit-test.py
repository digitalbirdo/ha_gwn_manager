"""Probe the request rate limit of a GWN Manager or GWN.Cloud open API.

    GWN_URL=https://eu.gwn.cloud GWN_APP_ID=... GWN_APP_SECRET=... \\
    python scripts/rate-limit-test.py [--rates 1,2,5,10,20] [--duration 30] [--no-verify-ssl]

Sends a cheap signed request (network list) at increasing rates, each for
--duration seconds, and reports per rate how many requests succeeded, which
retCodes / HTTP statuses came back and the latency. Stops at the first rate
with errors unless --keep-going is given. Read-only: never changes anything.
Credentials come from the environment so they don't end up in shell history.
Needs only aiohttp (e.g. the dev instance's venv).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import statistics
import time
from collections import Counter
from pathlib import Path

import aiohttp

# Load api.py directly: importing the package would pull in Home Assistant.
_API = Path(__file__).resolve().parent.parent / "custom_components/gwn_manager/api.py"
_spec = importlib.util.spec_from_file_location("gwn_api", _API)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

ENDPOINT = "/v1.0.0/network/list"
BODY = {"type": "asc", "order": "id", "search": "", "pageNum": 1, "pageSize": 10}


async def one_request(client: api.GWNClient) -> tuple[str, float]:
    start = time.monotonic()
    try:
        # retry=False: we want to see every error, not hide it behind a retry.
        await client._post(ENDPOINT, BODY, retry=False)
        result = "ok"
    except api.GWNApiError as err:
        result = f"retCode {err.ret_code}: {err}"
    except api.GWNAuthError as err:
        result = f"auth: {err}"
    except api.GWNConnectionError as err:
        cause = err.__cause__
        status = getattr(cause, "status", None)
        result = f"HTTP {status}" if status else f"connection: {err}"
    except Exception as err:  # noqa: BLE001 - report everything the API does
        result = f"{type(err).__name__}: {err}"
    return result, time.monotonic() - start


async def run_rate(client: api.GWNClient, rate: float, duration: float) -> Counter:
    tasks: list[asyncio.Task] = []
    interval = 1 / rate
    start = time.monotonic()
    n = 0
    while (next_at := start + n * interval) < start + duration:
        await asyncio.sleep(max(0, next_at - time.monotonic()))
        tasks.append(asyncio.create_task(one_request(client)))
        n += 1
    results = await asyncio.gather(*tasks)
    counts = Counter(r for r, _ in results)
    latencies = [t for _, t in results]
    print(
        f"{rate:>6.1f}/s  {len(results):>5} requests  "
        f"ok {counts['ok']:>5}  "
        f"latency median {statistics.median(latencies):.2f}s max {max(latencies):.2f}s"
    )
    for result, count in counts.items():
        if result != "ok":
            print(f"          {count:>5}x {result}")
    return counts


async def main(args: argparse.Namespace) -> None:
    connector = aiohttp.TCPConnector(ssl=None if args.verify_ssl else False)
    async with aiohttp.ClientSession(connector=connector) as session:
        client = api.GWNClient(
            session, os.environ["GWN_URL"], os.environ["GWN_APP_ID"], os.environ["GWN_APP_SECRET"]
        )
        await client.authenticate()
        print(f"Token ok, {ENDPOINT}, {args.duration:g}s per rate, {args.pause:g}s pause")
        for i, rate in enumerate(args.rates):
            if i:
                await asyncio.sleep(args.pause)
            counts = await run_rate(client, rate, args.duration)
            if counts["ok"] != sum(counts.values()) and not args.keep_going:
                print(f"Errors at {rate:g}/s, stopping (--keep-going to continue).")
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--rates", type=lambda s: [float(x) for x in s.split(",")], default=[1, 2, 5, 10, 20]
    )
    parser.add_argument("--duration", type=float, default=30, help="seconds per rate")
    parser.add_argument("--pause", type=float, default=10, help="seconds between rates")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--no-verify-ssl", dest="verify_ssl", action="store_false")
    asyncio.run(main(parser.parse_args()))
