#!/usr/bin/env python3
"""
Debug script to test SSE connection.
"""

import asyncio
import aiohttp
import json

async def test_sse():
    uri = "http://localhost:8765/events"

    print(f"Connecting to {uri}...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(uri, timeout=aiohttp.ClientTimeout(total=None)) as response:
                print(f"Response status: {response.status}")
                print(f"Headers: {dict(response.headers)}")
                print("\n--- Receiving SSE events ---\n")

                event_count = 0
                buffer = ""

                async for line in response.content:
                    line = line.decode("utf-8")
                    buffer += line
                    print(f"Raw line: {repr(line)}")

                    # Check for complete event (empty line)
                    if line.strip() == "":
                        if buffer.strip():
                            event_count += 1
                            print(f"\n=== Event #{event_count} ===")
                            print(buffer)
                            print("=" * 40)
                        buffer = ""

                    # Limit for testing
                    if event_count >= 5:
                        print("\nReceived 5 events, stopping...")
                        break

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_sse())