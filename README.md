# async-remote

async-remote provides asynchronous remote access to Python objects. It serves the
same purpose as [Pyro](https://pyro5.readthedocs.io/en/latest/), with a focus
on asynchronous programming.

A server can register objects with async-remote. On the client side, async-remote provides
proxy objects that the client can use as if the server objects were in the
same application.

async-remote currently uses the CoAP protocol with the
[aiocoap](https://aiocoap.readthedocs.io) module for the network communication.

## Server and client example

### Server

```python
import asyncio, async_remote

class Counter:
    __async_remote_properties__ = {"value"}

    def __init__(self):
        self.value = 0

    @async_remote.expose
    def add(self, amount=1):
        self.value += amount
        return self.value

async def main():
    server = async_remote.Server(Counter())
    await server.serve()
    try:
        await asyncio.Event().wait()
    finally:
        await server.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### Client

```python
import asyncio
import async_remote

async def main():
    counter = await async_remote.AsyncObjectProxy.create("coap://localhost")
    try:
        print("Value:", await counter.value)
        print("After add:", await counter.add(3))
        print("Current value:", await counter.value)
    finally:
        await counter.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Exposing members

By default, `Server` uses `selected` access mode. Mark methods and computed
`@property` members with `@async_remote.expose`. Plain instance attributes can be
listed in `__async_remote_properties__`:

```python
import async_remote

class Device:
    __async_remote_properties__ = {"name", "temperature"}

    def __init__(self):
        self.name = "sensor-1"
        self.temperature = 21.5

    @async_remote.expose
    def reset(self):
        self.temperature = 0

server = async_remote.Server(Device())  # selected mode is the default
```

The client can read those attributes and invoke the exposed method:

```python
device = await async_remote.AsyncObjectProxy.create("coap://localhost")
try:
    print(await device.name)
    await device.reset()
finally:
    await device.close()
```

## Access modes

`Server(..., access_mode=...)` supports:

- `"selected"` (default): only names marked by `@async_remote.expose`,
  `__async_remote_properties__`, or `__async_remote_methods__` can be accessed.
- `"auto"`: public instance attributes and public methods are accessible
  automatically.
- `"unrestricted"`: attributes and methods, including private names, are
  accessible. Use this only for trusted rapid-prototyping environments.

## Remote object references

Objects already registered with the server are returned to clients as
proxies. Register them explicitly:

```python
server = async_remote.Server(root)
server.register(child)
```

Or enable automatic registration for any returned object, or only for chosen
classes:

```python
server = async_remote.Server(root, auto_register=True)
# Or restrict automatic registration to these types:
server = async_remote.Server(root, auto_register=[Child, AnotherChild])
```

Automatic registration applies when CBOR needs async-remote's object encoder for a
returned value. Standard CBOR-supported values such as strings, numbers,
lists, and dictionaries are serialized by value, not converted to remote
proxies. Registration doesn't expose members by itself: the returned
object's access mode still applies.

## Install

From a checkout:

```sh
python -m pip install .
```

For development and tests:

```sh
python -m pip install -e '.[test]'
python -m pytest
```

## Run tests in Podman

Build and run the test image from the repository root:

```sh
podman build -t async_remote-tests .
podman run --rm async_remote-tests
```

The image installs the package from `pyproject.toml` with its test
dependencies and runs pytest.
