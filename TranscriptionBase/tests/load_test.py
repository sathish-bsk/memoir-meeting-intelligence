#!/usr/bin/env python3
"""
Simple load test script for the transcription service.
Tests concurrency handling with configurable parallel requests.

Usage:
    python tests/load_test.py                  # Default: 5 requests, 2 concurrent
    python tests/load_test.py -n 10 -c 4       # 10 requests, 4 concurrent
    python tests/load_test.py --url http://localhost:8000
"""
import argparse
import asyncio
import time
from typing import List, Tuple

import httpx


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    request_num: int
) -> Tuple[int, int, str, float]:
    """Make a single request and return (num, status, request_id, elapsed)."""
    start = time.time()
    try:
        response = await client.post(
            f"{url}/transcribe",
            json={},
            timeout=30.0
        )
        elapsed = time.time() - start
        request_id = response.headers.get("X-Request-ID", "N/A")
        return (request_num, response.status_code, request_id, elapsed)
    except Exception as e:
        elapsed = time.time() - start
        return (request_num, 0, f"ERROR: {e}", elapsed)


async def run_load_test(
    url: str,
    total_requests: int,
    concurrency: int
) -> List[Tuple[int, int, str, float]]:
    """Run load test with specified concurrency."""
    results = []
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_request(client: httpx.AsyncClient, num: int):
        async with semaphore:
            return await make_request(client, url, num)

    async with httpx.AsyncClient() as client:
        tasks = [bounded_request(client, i + 1) for i in range(total_requests)]
        results = await asyncio.gather(*tasks)

    return results


def main():
    parser = argparse.ArgumentParser(description="Load test for transcription service")
    parser.add_argument("-n", "--requests", type=int, default=5, help="Total requests")
    parser.add_argument("-c", "--concurrency", type=int, default=2, help="Concurrent requests")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL")
    args = parser.parse_args()

    print(f"Running load test: {args.requests} requests, {args.concurrency} concurrent")
    print(f"Target: {args.url}")
    print("-" * 60)

    start = time.time()
    results = asyncio.run(run_load_test(args.url, args.requests, args.concurrency))
    total_elapsed = time.time() - start

    # Print results
    success = 0
    for num, status, req_id, elapsed in sorted(results):
        status_str = "OK" if status in (200, 400) else "FAIL"
        if status in (200, 400):
            success += 1
        print(f"Request {num:3d}: {status_str} ({status}) - {req_id[:8]}... - {elapsed:.3f}s")

    print("-" * 60)
    print(f"Total time: {total_elapsed:.2f}s")
    print(f"Success rate: {success}/{len(results)} ({100*success/len(results):.1f}%)")
    print(f"Requests/sec: {len(results)/total_elapsed:.2f}")

    # Check for unique request IDs
    request_ids = [r[2] for r in results if not r[2].startswith("ERROR")]
    unique_ids = len(set(request_ids))
    print(f"Unique request IDs: {unique_ids}/{len(request_ids)}")

    if unique_ids != len(request_ids):
        print("WARNING: Some request IDs are not unique - potential concurrency issue!")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
