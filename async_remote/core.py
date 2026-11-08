
"""CoAP transport implementation and its CBOR wire protocol.

Each request is a CBOR map. Its ``rt`` field means "request type" and tells
the server which operation to perform: ``pr`` reads an attribute, ``c`` calls
a method with ``n`` (its name) and ``a`` (its arguments), and ``idx`` lists
callable names. Replies wrap values as ``{"t": 0, "v": value}``; object
references use CBOR tag 1000 and resolve to another proxy.
"""

import aiocoap
import aiocoap.resource as resource
from .generic import RemoteError, accessible_members

debug = False

REQUEST_TYPE = "rt"
REQUEST_PROPERTY = "pr"
REQUEST_CALL = "c"
REQUEST_INDEX = "idx"
OBJECT_TAG = 1000
VALUE_REPLY_TYPE = 0

class CoapResource(resource.Resource):
	"""Dispatch AsyncRemote CBOR requests addressed to one registered object."""
	def __init__(self, server, obj, access_mode="auto"):
		super().__init__()
		
		self.server = server
		self.obj = obj
		self.access_mode = access_mode
		decorated_methods, self.allowed_properties = accessible_members(obj)
		self.allowed_methods = decorated_methods if access_mode == "selected" else {
			n for n in dir(obj) if not n.startswith("_") and callable(getattr(obj, n))
		}

	async def render(self, request):
		if debug:
			print("rx", request.opt.uri_path)
		
		try:
			data = await self.server.loads(request.payload)
		except Exception:
			return aiocoap.Message(code=aiocoap.BAD_REQUEST)
		if not isinstance(data, dict):
			return aiocoap.Message(code=aiocoap.BAD_REQUEST)
		
		# ``pr`` is a GET-style attribute read. The access mode is checked
		# before getattr because properties can execute arbitrary Python code.
		if data.get(REQUEST_TYPE) == REQUEST_PROPERTY:
			if not data.get("n", None):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			
			name = data["n"]
			plain_instance_attribute = isinstance(name, str) and name in getattr(self.obj, "__dict__", {})
			decorated_member = isinstance(name, str) and (name in self.allowed_properties or name in self.allowed_methods)
			if (not isinstance(name, str) or
				(self.access_mode != "unrestricted" and name.startswith("_")) or
				(self.access_mode == "selected" and not decorated_member) or
				(self.access_mode == "auto" and name not in self.allowed_methods and not plain_instance_attribute and not isinstance(getattr(type(self.obj), name, None), property)) or
				not hasattr(self.obj, name)):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			
			prop = getattr(self.obj, name)
			
			if callable(prop):
				payload = self.server.dumps({"t": VALUE_REPLY_TYPE, "v": "proxy", "c": True})
			else:
				payload = self.server.dumps({"t": VALUE_REPLY_TYPE, "v": prop, "c": False})
			
			return aiocoap.Message(payload=payload, code=aiocoap.CONTENT)
		# Calls use POST because invoking a method can change remote state.
		elif data.get(REQUEST_TYPE) == REQUEST_CALL and request.code == aiocoap.POST:
			if not data.get("n", None):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			
			name = data["n"]
			if not isinstance(name, str) or (self.access_mode != "unrestricted" and name not in self.allowed_methods) or not hasattr(self.obj, name):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			
			call_args = data.get("a")
			# ``a`` is encoded as the pair ``(positional_args, keyword_args)``.
			if (not isinstance(name, str) or (self.access_mode != "unrestricted" and name not in self.allowed_methods) or
				not isinstance(call_args, (list, tuple)) or len(call_args) != 2 or
				not isinstance(call_args[0], (list, tuple)) or not isinstance(call_args[1], dict)):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			args, kwargs = call_args
			
			fct = getattr(self.obj, name)
			if not callable(fct):
				return aiocoap.Message(code=aiocoap.BAD_REQUEST)
			try:
				result = fct(*args, **kwargs)
				if hasattr(result, "__await__"):
					result = await result
				payload = self.server.dumps({"t":0, "v": result})
			except Exception as exc:
				payload = self.server.dumps({"error": type(exc).__name__, "message": str(exc)})
				return aiocoap.Message(payload=payload, code=aiocoap.INTERNAL_SERVER_ERROR)
			
			return aiocoap.Message(payload=payload, code=aiocoap.CONTENT)
		# The client uses this list to return callable proxies without a
		# separate attribute request for every method.
		elif data.get(REQUEST_TYPE) == REQUEST_INDEX:
			callables = []
			callables = sorted(n for n in dir(self.obj) if callable(getattr(self.obj, n)) and (self.access_mode == "unrestricted" or n in self.allowed_methods))
			
			payload = self.server.dumps({"t":0, "v": callables})
			
			return aiocoap.Message(payload=payload, code=aiocoap.CONTENT)
		else:
			return aiocoap.Message(code=aiocoap.BAD_REQUEST)

class AsyncRemoteBase():
	# Tag 1000 denotes a server-side object reference, not a serialized copy.
	cbor2_obj_tag = OBJECT_TAG
	cbor2_tuple_tag = 1001
	
	def __init__(self):
		self.local_objs = {}
	
	async def loads(self, data):
		from cbor2 import CBORTag, loads
		
		# CBOR decodes synchronously. Build references first, then initialize
		# their nested proxies asynchronously once the decode is complete.
		postponed_init = []
		
		def tag_hook(cbor_tag, immutable):
			if cbor_tag.tag == self.cbor2_obj_tag:
				if cbor_tag.value.startswith("coap"):
					ac = AsyncObjectProxy(uri=cbor_tag.value)
				else:
					ac = AsyncObjectProxy(host=self.host, obj_path=cbor_tag.value, root_proxy=self)
				
				postponed_init.append(ac)
				
				return ac
			
			return cbor_tag
		
		result = loads(data, tag_hook=tag_hook)
		
		for ac in postponed_init:
			await ac.init()
		
		return result
	
	def dumps(self, data):
		from cbor2 import dumps, CBORTag
		
		def default_encoder(encoder, value):
			if isinstance(value, AsyncObjectProxy):
				if value.host == self.host:
					tag = CBORTag(self.cbor2_obj_tag, value.obj_path)
				else:
					tag = CBORTag(self.cbor2_obj_tag, value.uri)
				encoder.encode(tag)
			else:
				valkey = None
				for key, val in self.local_objs.items():
					if val is value:
						valkey = key
						break
				if valkey is None:
					auto_register = getattr(self, "auto_register", False)
					allowed_types = getattr(self, "auto_register_types", None)
					if auto_register and (allowed_types is None or isinstance(value, allowed_types)):
						valkey = self.register(value)
					else:
						raise TypeError("object is not registered or allowed for auto-registration: {!r}".format(value))
				
				tag = CBORTag(self.cbor2_obj_tag, valkey)
				encoder.encode(tag)
		
		return dumps(data, default=default_encoder)

class Server(AsyncRemoteBase):
	"""Expose registered objects over CoAP."""
	def __init__(self, root_obj, bind=None, psks=None, access_mode="selected", auto_register=False):
		super().__init__()
		
		self.bind = bind
		self.psks = psks
		if access_mode not in {"auto", "selected", "unrestricted"}:
			raise ValueError("access_mode must be 'auto', 'selected', or 'unrestricted'")
		self.access_mode = access_mode
		if isinstance(auto_register, bool):
			self.auto_register = auto_register
			self.auto_register_types = None
		else:
			if isinstance(auto_register, type):
				auto_register = (auto_register,)
			try:
				self.auto_register_types = tuple(auto_register)
			except TypeError as exc:
				raise TypeError("auto_register must be a bool or an iterable of classes") from exc
			self.auto_register = True
		if self.auto_register_types is not None and not all(isinstance(cls, type) for cls in self.auto_register_types):
			raise TypeError("auto_register must be a bool or an iterable of classes")
		self.next_id = 0
		self.context = None
		self.root = resource.Site()
		self.register(root_obj)
	
	async def serve(self):
		self.context = await aiocoap.Context.create_server_context(
			self.root,
			bind=self.bind,
			)
		return self.context

	async def close(self):
		if self.context is not None:
			await self.context.shutdown()
			self.context = None
	
	def register(self, obj, path=None):
		"""Publish an object under a local path and return its path key."""
		if not path:
			path = ["%x" % self.next_id]
			self.next_id += 1
		
		coap_path = "/".join(path)
		if coap_path in self.local_objs:
			raise Exception(path)
		
		if debug:
			print("register", obj, "as", coap_path)
		
		self.local_objs[coap_path] = obj
		
		rs = CoapResource(self, obj, access_mode=self.access_mode)
		
		self.root.add_resource(["objs"]+path, rs)
		
		return coap_path
	
	def unregister(self, obj):
		valkey = None
		for key, val in self.local_objs.items():
			if val == obj:
				valkey = key
				break
		
		del self.root._resources[tuple(["objs"])+tuple(valkey.split("/"))]
		del self.local_objs[valkey]

class AsyncCallableProxy():
	"""Awaitable wrapper that sends one remote method call."""
	def __init__(self, client, name):
		super().__init__()
		
		self.client = client
		self.name = name
	
	async def __call__(self, *args, **kwargs):
		if debug:
			print("call", self.name)
		
		payload = self.client.dumps({REQUEST_TYPE: REQUEST_CALL, "n": self.name, "a": (args, kwargs)})
		request = aiocoap.Message(code=aiocoap.POST, uri=self.client.uri, payload=payload)
		
		response = await self.client.context.request(request).response
		if response.code != aiocoap.CONTENT:
			try:
				details = await self.client.loads(response.payload)
			except Exception:
				details = {}
			raise RemoteError("{}: {}".format(details.get("error", response.code), details.get("message", "remote call failed")))
		data = await self.client.loads(response.payload)
		
		return data.get("v", None)
class AsyncObjectProxy(AsyncRemoteBase):
	"""Async attribute and method proxy for a remotely registered object."""
	def __init__(self, host=None, obj_path=None, uri=None, root_proxy=None, cache_callables=True):
		super().__init__()
		
		self.root_proxy = root_proxy
		self.cache_callables = cache_callables
		self.callables_to = False
		self.callables = []
		self.context = None
		
		if host is None:
			self.host = "coap://localhost"
		else:
			self.host = host
		
		if obj_path is None:
			self.obj_path = "0"
		else:
			self.obj_path = obj_path
		
		if uri is None:
			self.uri = self.host + "/objs/" + self.obj_path
		else:
			self.uri = uri
	
	async def init(self):
		if self.cache_callables and not self.callables_to:
			await self.queryCallables()
			self.callables_to = True
	
	@staticmethod
	async def create(*args, **kwargs):
		ac = AsyncObjectProxy(*args, **kwargs)
		await ac.init()
		
		return ac
	
	async def create_context(self):
		if self.context is not None:
			return
		
		if self.root_proxy is None:
			self.context = await aiocoap.Context.create_client_context()
		else:
			self.context = self.root_proxy.context
		
	
	async def queryCallables(self):
		payload = self.dumps({REQUEST_TYPE: REQUEST_INDEX})
		request = aiocoap.Message(code=aiocoap.GET, uri=self.uri, payload=payload)
		
		if self.context is None:
			await self.create_context()
		
		response = await self.context.request(request).response
		if response.code != aiocoap.CONTENT:
			raise Exception(response.code)
		
		data = await self.loads(response.payload)
		self.callables = data.get("v", [])
		
	
	async def get(self, name):
		if debug:
			print("get", name)
		
		await self.init()
		
		payload = self.dumps({REQUEST_TYPE: REQUEST_PROPERTY, "n": name})
		request = aiocoap.Message(code=aiocoap.GET, uri=self.uri, payload=payload)
		
		if self.context is None:
			await self.create_context()
		
		response = await self.context.request(request).response
		if response.code != aiocoap.CONTENT:
			raise Exception(response.code)
		
		data = await self.loads(response.payload)
		if "t" not in data:
			raise Exception(data)
		
		if data["t"] == 0:
			if data.get("c", False) is False:
				return data["v"]
			else:
				return AsyncCallableProxy(self, name)
		
		raise Exception()
	
	def __getattr__(self, name):
		if name.startswith("_"):
			raise AttributeError(name)
		if self.cache_callables:
			if name in self.callables:
				return AsyncCallableProxy(self, name)
		
		return self.get(name)

	async def close(self):
		if self.root_proxy is None and self.context is not None:
			await self.context.shutdown()
			self.context = None
