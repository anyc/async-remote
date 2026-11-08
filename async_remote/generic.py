"""Transport-independent remote access metadata and errors."""

class RemoteError(RuntimeError):
	"""An error reported by a remote AsyncRemote object."""

def expose(member):
	"""Mark a method or property as remotely accessible."""
	target = member.fget if isinstance(member, property) else member
	target.__async_remote_exposed__ = True
	return member

def accessible_members(obj):
	"""Return explicitly exposed method and property names for an object."""
	methods = set(getattr(obj, "__async_remote_methods__", ()))
	properties = set(getattr(obj, "__async_remote_properties__", ()))

	for name in dir(type(obj)):
		member = getattr(type(obj), name, None)
		target = member.fget if isinstance(member, property) else member
		if not getattr(target, "__async_remote_exposed__", False):
			continue
		if callable(getattr(obj, name, None)):
			methods.add(name)
		else:
			properties.add(name)

	return methods, properties
