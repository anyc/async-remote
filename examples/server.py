"""Run with: python examples/server.py"""

import asyncio
import async_remote

class Counter:
	__async_remote_properties__ = {"value"}

	def __init__(self):
		self.value = 0

	@async_remote.expose
	def add(self, amount=1):
		self.value += amount
		return self.value

async def main():
	server = async_remote.Server(Counter(), access_mode="selected")
	await server.serve()
	print("AsyncRemote server listening on coap://localhost:5683")
	try:
		await asyncio.Event().wait()
	finally:
		await server.close()

if __name__ == "__main__":
	asyncio.run(main())
