"""Scale and lightweight performance coverage for large object registries."""

import os
import socket
import time
import tracemalloc

import pytest

import async_remote


class ScaleItem:
	__async_remote_methods__ = {"identity"}

	def __init__(self, index):
		self.index = index

	def identity(self):
		return self.index


def free_udp_port():
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
		sock.bind(("127.0.0.1", 0))
		return sock.getsockname()[1]


def resident_bytes():
	"""Return current process RSS on Linux, or None when unavailable."""
	try:
		with open("/proc/self/statm", encoding="ascii") as statm:
			resident_pages = int(statm.read().split()[1])
		return resident_pages * os.sysconf("SC_PAGE_SIZE")
	except (OSError, IndexError, ValueError):
		return None


@pytest.mark.asyncio
@pytest.mark.performance
async def test_large_registry_registration_and_remote_call(record_property):
	object_count = int(os.environ.get("ASYNC_REMOTE_SCALE_OBJECTS", "1000"))
	assert object_count > 0

	root = ScaleItem(-1)
	server = async_remote.Server(root)
	tracemalloc.start()
	python_heap_before, _ = tracemalloc.get_traced_memory()
	rss_before = resident_bytes()
	started = time.perf_counter()
	items = [ScaleItem(index) for index in range(object_count)]
	for index, item in enumerate(items):
		server.register(item, path=["scale", str(index)])
	registration_seconds = time.perf_counter() - started
	python_heap_after, python_heap_peak = tracemalloc.get_traced_memory()
	rss_after = resident_bytes()
	tracemalloc.stop()
	heap_delta = python_heap_after - python_heap_before
	heap_peak_delta = python_heap_peak - python_heap_before
	rss_delta = rss_after - rss_before if rss_before is not None and rss_after is not None else None
	heap_per_object = heap_delta / object_count
	heap_peak_per_object = heap_peak_delta / object_count
	rss_per_object = rss_delta / object_count if rss_delta is not None else None

	port = free_udp_port()
	server.bind = ("127.0.0.1", port)
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(
		host=f"coap://127.0.0.1:{port}"
	)
	try:
		# Reuse the connected client context. Each object call below performs
		# exactly one CoAP POST; callable discovery is already known to this test.
		remote_items = [
			async_remote.AsyncObjectProxy(
				host=f"coap://127.0.0.1:{port}",
				obj_path=f"scale/{index}",
				root_proxy=client,
			)
			for index in range(object_count)
		]
		for remote_item in remote_items:
			remote_item.context = client.context
			remote_item.callables = ["identity"]
			remote_item.callables_to = True

		started = time.perf_counter()
		results = [await item.identity() for item in remote_items]
		remote_call_seconds = time.perf_counter() - started
		assert results == list(range(object_count))
	finally:
		await client.close()
		await server.close()

	record_property("registered_objects", object_count)
	record_property("registration_seconds", registration_seconds)
	record_property("registration_objects_per_second", object_count / registration_seconds)
	record_property("remote_requests", object_count)
	record_property("last_object_call_seconds", remote_call_seconds)
	record_property("python_heap_delta_bytes", heap_delta)
	record_property("python_heap_peak_delta_bytes", heap_peak_delta)
	record_property("python_heap_bytes_per_object", heap_per_object)
	record_property("python_heap_peak_bytes_per_object", heap_peak_per_object)
	if rss_delta is not None:
		record_property("process_rss_delta_bytes", rss_delta)
		record_property("process_rss_bytes_per_object", rss_per_object)
	print(
		f"registered={object_count} in {registration_seconds:.6f}s "
		f"({object_count / registration_seconds:.0f} objects/s); "
		f"requests={object_count} in {remote_call_seconds:.6f}s "
		f"({object_count / remote_call_seconds:.0f} requests/s); "
		f"python_heap={heap_delta}B ({heap_per_object:.1f}B/object), "
		f"python_heap_peak={heap_peak_delta}B ({heap_peak_per_object:.1f}B/object), "
		f"rss={rss_delta if rss_delta is not None else 'unavailable'}B "
		f"({rss_per_object:.1f}B/object)"
	)
