
import socket

import async_remote
import pytest

class Example:
	def __init__(self):
		self.label = "hello"

	def greet(self, who="world"):
		return f"{self.label} {who}"

	async def async_greet(self, who):
		return f"{self.label} {who}"


class DecoratedExample:
	def __init__(self):
		self.secret = "visible"
		self.instance_value = "plain attribute"

	@property
	@async_remote.expose
	def public_value(self):
		return self.secret

	@async_remote.expose
	def exposed(self):
		return "yes"

	def hidden(self):
		return "no"

	@async_remote.expose
	def make_child(self):
		return Child()


class Child:
	@async_remote.expose
	def ping(self):
		return "pong"


class ChangingResult:
	def __init__(self):
		self.value = 0

	@async_remote.expose
	def next_value(self):
		self.value += 1
		return self.value


class CallbackReceiver:
	def __init__(self):
		self.result = None

	@async_remote.expose
	def completed(self, value):
		self.result = value
		return "received " + value


class CallbackTask:
	@async_remote.expose
	async def finish(self, callback):
		callback_method = await callback.completed
		return await callback_method("done")


def free_udp_port():
	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
		sock.bind(("127.0.0.1", 0))
		return sock.getsockname()[1]


@pytest.fixture
def example():
	return Example()


def test_cbor_round_trip(example):
	server = async_remote.Server(example)
	encoded = server.dumps({"value": [1, "two", ("three",)]})
	assert __import__("cbor2").loads(encoded) == {"value": [1, "two", ["three"]]}


def test_proxy_uses_callback_tag_for_other_host():
	from cbor2 import CBORTag, loads

	sender = async_remote.AsyncObjectProxy(host="coap://sender.example")
	proxy = async_remote.AsyncObjectProxy(
		uri="coap://callback.example:5683/callback",
		root_proxy=sender,
	)
	encoded = sender.dumps(proxy)
	tag = loads(encoded)
	assert isinstance(tag, CBORTag)
	assert tag.tag == 1001
	assert tag.value == "0"


@pytest.mark.asyncio
async def test_coap_proxy_properties_methods_and_nested_objects(example):
	child = Example()
	example.child = child
	port = free_udp_port()
	server = async_remote.Server(example, bind=("127.0.0.1", port), access_mode="auto")
	server.register(child)
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}", cache_callables=True)
	try:
		assert await client.label == "hello"
		assert await client.greet("async_remote") == "hello async_remote"
		assert await client.async_greet("async") == "hello async"
		nested = await client.child
		assert await nested.greet() == "hello world"
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_remote_method_exception_is_reported(example):
	def fail():
		raise ValueError("expected failure")

	example.fail = fail
	port = free_udp_port()
	server = async_remote.Server(example, bind=("127.0.0.1", port), access_mode="auto")
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		remote_fail = client.fail
		with pytest.raises(async_remote.RemoteError, match="ValueError: expected failure"):
			await remote_fail()
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_decorators_expose_selected_method_and_property():
	port = free_udp_port()
	server = async_remote.Server(DecoratedExample(), bind=("127.0.0.1", port), access_mode="auto")
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		assert await client.public_value == "visible"
		assert await client.instance_value == "plain attribute"
		assert await client.exposed() == "yes"
		with pytest.raises(Exception):
			await client.hidden
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_selected_access_mode_allows_only_exposed_members():
	port = free_udp_port()
	server = async_remote.Server(DecoratedExample(), bind=("127.0.0.1", port), access_mode="selected")
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		assert await client.public_value == "visible"
		with pytest.raises(Exception):
			await client.instance_value
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_auto_registers_objects_returned_by_methods():
	port = free_udp_port()
	server = async_remote.Server(DecoratedExample(), bind=("127.0.0.1", port), auto_register=True)
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		child = await client.make_child()
		assert await child.ping() == "pong"
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_auto_register_class_allowlist():
	port = free_udp_port()
	server = async_remote.Server(
		DecoratedExample(), bind=("127.0.0.1", port), auto_register=[Child],
	)
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		child = await client.make_child()
		assert await child.ping() == "pong"
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_auto_register_class_allowlist_rejects_other_classes():
	port = free_udp_port()
	server = async_remote.Server(
		DecoratedExample(), bind=("127.0.0.1", port), auto_register=[dict],
	)
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		with pytest.raises(async_remote.RemoteError, match="TypeError"):
			await client.make_child()
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_repeated_access_to_same_object_returns_updated_results():
	port = free_udp_port()
	server = async_remote.Server(ChangingResult(), bind=("127.0.0.1", port))
	await server.serve()
	client = await async_remote.AsyncObjectProxy.create(host=f"coap://127.0.0.1:{port}")
	try:
		assert await client.next_value() == 1
		assert await client.next_value() == 2
		assert await client.next_value() == 3
	finally:
		await client.close()
		await server.close()


@pytest.mark.asyncio
async def test_client_object_callback_round_trip():
	callback_port = free_udp_port()
	service_port = free_udp_port()
	callback_object = CallbackReceiver()
	callback_server = async_remote.Server(
		callback_object, bind=("127.0.0.1", callback_port)
	)
	service_server = async_remote.Server(
		CallbackTask(), bind=("127.0.0.1", service_port)
	)
	await callback_server.serve()
	await service_server.serve()
	client = async_remote.AsyncObjectProxy(
		host=f"coap://127.0.0.1:{service_port}",
		context=callback_server.context,
	)
	await client.init()
	callback_proxy = async_remote.AsyncObjectProxy(
		host=f"coap://127.0.0.1:{callback_port}",
		obj_path="0",
		root_proxy=client,
	)
	try:
		assert await client.finish(callback_proxy) == "received done"
		assert callback_object.result == "done"
	finally:
		await client.close()
		await service_server.close()
		await callback_server.close()
