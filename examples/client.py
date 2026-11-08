"""Run with: python examples/client.py (while the example server is running)."""

import asyncio
import async_remote

async def main():
	counter = await async_remote.AsyncObjectProxy.create("coap://localhost")
	try:
		print("Instance attribute:", await counter.value)
		print("After:", await counter.add(3))
		print("Now:", await counter.value)
	finally:
		await counter.close()

if __name__ == "__main__":
	asyncio.run(main())
