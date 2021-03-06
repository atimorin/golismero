#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
GoLismero data model.
"""

__license__ = """
GoLismero 2.0 - The web knife - Copyright (C) 2011-2013

Authors:
  Daniel Garcia Garcia a.k.a cr0hn | cr0hn<@>cr0hn.com
  Mario Vilas | mvilas<@>gmail.com

Golismero project site: https://github.com/golismero
Golismero project mail: golismero.project<@>gmail.com

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""

__all__ = ["Data", "identity", "merge", "overwrite", "LocalDataCache"]

from .db import Database
from ..config import Config
from ...common import pickle, Singleton

from collections import defaultdict
from functools import partial
from hashlib import md5
from uuid import uuid4
from warnings import warn


#------------------------------------------------------------------------------
class identity(property):
    """
    Decorator that marks read-only properties as part of the object's identity.

    It may not be combined with any other decorator, and may not be subclassed.
    """

    def __init__(self, fget = None, doc = None):
        property.__init__(self, fget, doc = doc)

    def setter(self):
        raise AttributeError("can't set attribute")

    def deleter(self):
        raise AttributeError("can't delete attribute")

    @staticmethod
    def is_identity_property(other):
        """
        Determine if a class property is marked with the @identity decorator.

        :param other: Class property.
        :type other: property

        :returns: True if the property is marked, False otherwise.
        :rtype: bool
        """

        # TODO: benchmark!!!
        ##return isinstance(other, identity)
        ##return getattr(other, "is_identity_property", None) is not None
        ##return hasattr(other, "is_identity_property")

        try:
            other.__get__
            other.is_identity_property
            return True
        except AttributeError:
            return False


#------------------------------------------------------------------------------
class merge(property):
    """
    Decorator that marks properties that can be merged safely.

    It may not be combined with any other decorator, and may not be subclassed.
    """

    @staticmethod
    def is_mergeable_property(other):
        """
        Determine if a class property is marked with the @merge decorator.

        :param other: Class property.
        :type other: property

        :returns: True if the property is marked, False otherwise.
        :rtype: bool
        """

        # TODO: benchmark!!!
        ##return isinstance(other, merge) and other.fset is not None

        try:
            other.__get__
            other.is_mergeable_property
            return other.fset is not None
        except AttributeError:
            return False


#------------------------------------------------------------------------------
class overwrite(property):
    """
    Decorator that marks properties that can be overwritten safely.

    It may not be combined with any other decorator, and may not be subclassed.
    """

    @staticmethod
    def is_overwriteable_property(other):
        """
        Determine if a class property is marked with the @overwrite decorator.

        :param other: Class property.
        :type other: property

        :returns: True if the property is marked, False otherwise.
        :rtype: bool
        """

        # TODO: benchmark!!!
        ##return isinstance(other, overwrite) and other.fset is not None

        try:
            other.__get__
            other.is_overwriteable_property
            return other.fset is not None
        except AttributeError:
            return False


#------------------------------------------------------------------------------
def discard_data(data):
    """
    Plugins may call this function to indicate the given Data object is no
    longer of interest and may be safely discarded.

    When plugins create Data objects but do not return them in their
    recv_info() method, a warning is issued automatically. This method allows
    plugins to remove that warning when they do not intend for a Data object
    to be returned, but just created it temporarily or discarded it for some
    other reason.

    .. warning: Use with care! If you mark an object as discarded but another
        Data object has a reference to it, the audit database may be left in
        an inconsistent state.

    :param data: Data object to mark as discarded.
    :type data: Data
    """
    if hasattr(data, "identity"):
        data = data.identity
    if type(data) is not str:
        raise TypeError("Expected Data, got %s instead" % type(data))
    LocalDataCache.discard(data)


#------------------------------------------------------------------------------
class _data_metaclass(type):
    """
    Metaclass to validate the definitions of Data subclasses.

    .. warning: Do not use! If you want to define your own Data subclasses,
                just derive from Information, Resource or Vulnerability.
    """

    def __init__(cls, name, bases, namespace):
        super(_data_metaclass, cls).__init__(name, bases, namespace)

        # Skip checks for the base classes.
        if cls.__module__ in (
            "golismero.api.data",
            "golismero.api.data.information",
            "golismero.api.data.resource",
            "golismero.api.data.vulnerability",
        ):
            return

        # Check the data_type is not TYPE_UNKNOWN.
        if not cls.data_type:
            msg = "Error in %s.%s: Subclasses of Data MUST define their data_type!"
            raise TypeError(msg % (cls.__module__, cls.__name__))

        # Check the information_type is not INFORMATION_UNKNOWN.
        if cls.data_type == Data.TYPE_INFORMATION:
            if not cls.information_type:
                msg = "Error in %s.%s: Subclasses of Information MUST define their information_type!"
                raise TypeError(msg % (cls.__module__, cls.__name__))

        # Check the resource_type is not RESOURCE_UNKNOWN.
        elif cls.data_type == Data.TYPE_RESOURCE:
            if not cls.resource_type:
                msg = "Error in %s.%s: Subclasses of Resource MUST define their resource_type!"
                raise TypeError(msg % (cls.__module__, cls.__name__))

        # Automatically calculate the vulnerability type from the module name.
        elif cls.data_type == Data.TYPE_VULNERABILITY:
            is_vuln_type_missing = "vulnerability_type" not in cls.__dict__
            if cls.__module__.startswith("golismero.api.data.vulnerability."):
                if is_vuln_type_missing:
                    vuln_type = cls.__module__[33:]
                    vuln_type = vuln_type.replace(".", "/")
                    cls.vulnerability_type = vuln_type

            # If we can't, at least make sure it's defined manually.
            elif is_vuln_type_missing:
                msg = "Error in %s.%s: Subclasses of Vulnerability MUST define their vulnerability_type!"
                raise TypeError(msg % (cls.__module__, cls.__name__))

        # Check all @merge and @overwrite properties have setters.
        for name, prop in cls.__dict__.iteritems():
            if merge.is_mergeable_property(prop):
                if prop.fset is None:
                    msg = "Error in %s.%s.%s: Properties tagged with @merge MUST have a setter!"
                    raise TypeError(msg % (cls.__module__, cls.__name__, name))
            elif overwrite.is_overwriteable_property(prop):
                if prop.fset is None:
                    msg = "Error in %s.%s.%s: Properties tagged with @overwrite MUST have a setter!"
                    raise TypeError(msg % (cls.__module__, cls.__name__, name))


#------------------------------------------------------------------------------
class Data(object):
    """
    Base class for all data elements.

    .. warning: Do not subclass directly!
                If you want to define your own Data subclasses,
                derive from Information, Resource or Vulnerability.
    """

    __metaclass__ = _data_metaclass

    # TODO: Add user-defined tags to Data objects.
    # TODO: Add user-defined properties to Data objects.


    #--------------------------------------------------------------------------
    # Data types

    TYPE_UNKNOWN = 0      # not a real type! only used in get_accepted_info()

    TYPE_INFORMATION           = 1
    TYPE_VULNERABILITY         = 2
    TYPE_RESOURCE              = 3

    data_type = TYPE_UNKNOWN


    #----------------------------------------------------------------------
    # Minimum number of linked objects per data type.
    # Use None to enforce no limits.

    min_data = None              # Minimum for all data types.
    min_resources = None         # Minimum linked resources.
    min_informations = None      # Minimum linked informations.
    min_vulnerabilities = None   # Minimum linked vulnerabilities.


    #----------------------------------------------------------------------
    # Maximum number of linked objects per data type.
    # Use None to enforce no limits.

    max_data = None              # Maximum for all data types.
    max_resources = None         # Maximum linked resources.
    max_informations = None      # Maximum linked informations.
    max_vulnerabilities = None   # Maximum linked vulnerabilities.


    #----------------------------------------------------------------------
    def __init__(self):

        # Linked Data objects.
        # + all links:                  None -> None -> set(identity)
        # + links by type:              type -> None -> set(identity)
        # + links by type and subtype:  type -> subtype -> set(identity)
        self.__linked = defaultdict(partial(defaultdict, set))

        # Identity hash cache.
        self.__identity = None

        # Tell the temporary storage about this instance.
        LocalDataCache.on_create(self)


    #----------------------------------------------------------------------
    def __repr__(self):
        return "<%s identity=%s>" % (self.__class__.__name__, self.identity)


    #----------------------------------------------------------------------
    @property
    def identity(self):
        """
        :returns: Identity hash of this object.
        :rtype: str
        """

        # If the hash is already in the cache, return it.
        if self.__identity is not None:
            return self.__identity

        # Build a dictionary of all properties
        # marked as part of the identity.
        collection = self._collect_identity_properties()

        # If there are identity properties, add the class name too.
        # That way two objects of different classes will never have
        # the same identity hash.
        if collection:
            classname = self.__class__.__name__
            if '.' in classname:
                classname = classname[ classname.rfind('.') + 1 : ]
            collection[""] = classname

        # If there are no identity properties, use a random UUID instead.
        # This makes all unidentifiable objects unique.
        else:
            collection = uuid4()

        # Pickle the data with the compatibility protocol.
        # This produces always the same result for the same input data.
        data = pickle.dumps(collection, protocol = 0)

        # Calculate the MD5 hash of the pickled data.
        hash_sum = md5(data)

        # Calculate the hexadecimal digest of the hash.
        hex_digest = hash_sum.hexdigest()

        # Store it in the cache.
        self.__identity = hex_digest

        # Return it.
        return self.__identity


    # Protected method, we don't want outsiders calling it.
    # Subclasses may need to override it, but let's hope not!
    def _collect_identity_properties(self):
        """
        Returns a dictionary of identity properties
        and their values for this data object.

        .. warning: This is an internally used method. Do not call!

        :returns: Collected property names and values.
        :rtype: dict(str -> *)
        """
        is_identity_property = identity.is_identity_property
        clazz = self.__class__
        collection = {}
        for key in dir(self):
            if not key.startswith("_") and key != "identity":
                prop = getattr(clazz, key, None)
                if prop is not None and is_identity_property(prop):
                    # ASCII or UTF-8 is assumed for all strings!
                    value = prop.__get__(self)
                    if isinstance(value, unicode):
                        try:
                            value = value.encode("UTF-8")
                        except UnicodeError:
                            pass
                    collection[key] = value
        return collection


    #----------------------------------------------------------------------
    def merge(self, other):
        """
        Merge another data object with this one.

        This is the old data, and the other object is the new data.

        :param other: Data object to merge with this one.
        :type other: Data
        """
        self._merge_objects(self, other, reverse = False)


    def reverse_merge(self, other):
        """
        Reverse merge another data object with this one.

        This is the new data, and the other object is the old data.

        :param other: Data object to be merged with this one.
        :type other: Data
        """
        self._merge_objects(other, self, reverse = True)


    @classmethod
    def _merge_objects(cls, old_data, new_data, reverse = False):
        """
        Merge objects in any order.

        .. warning: This is an internally used method. Do not call!

        :param old_data: Old data object.
        :type old_data: Data

        :param new_data: New data object.
        :type new_data: Data

        :param reverse: Merge order flag.
        :type reverse: bool
        """

        # Type checks.
        if type(old_data) is not type(new_data):
            raise TypeError("Can only merge data objects of the same type")
        if old_data.identity != new_data.identity:
            raise ValueError("Can only merge data objects of the same identity")

        # Merge the properties.
        for key in dir(new_data):
            if not key.startswith("_") and key != "identity":
                cls._merge_property(old_data, new_data, key, reverse = reverse)

        # Merge the links.
        cls._merge_links(old_data, new_data, reverse = reverse)


    @classmethod
    def _merge_property(cls, old_data, new_data, key, reverse = False):
        """
        Merge a single property.

        .. warning: This is an internally used method. Do not call!

        :param old_data: Old data object.
        :type old_data: Data

        :param new_data: New data object.
        :type new_data: Data

        :param key: Property name.
        :type key: str

        :param reverse: Merge order flag.
        :type reverse: bool
        """

        # Determine if the property is mergeable or overwriteable, ignore otherwise.
        prop = getattr(new_data.__class__, key, None)

        # Merge strategy.
        if prop is None or merge.is_mergeable_property(prop):

            # Get the original value.
            my_value = getattr(old_data, key, None)

            # Get the new value.
            their_value = getattr(new_data, key, None)

            # None to us means "not set".
            if their_value is not None:

                # If the original value is not set, overwrite it always.
                if my_value is None:
                    my_value = their_value

                # Combine sets, dictionaries, lists and tuples.
                elif isinstance(their_value, (set, dict)):
                    if reverse:
                        my_value = my_value.copy()
                    my_value.update(their_value)
                elif isinstance(their_value, list):
                    if reverse:
                        my_value = my_value + their_value
                    else:
                        my_value.extend(their_value)
                elif isinstance(their_value, tuple):
                    my_value = my_value + their_value

                # Overwrite all other types.
                else:
                    my_value = their_value

                # Set the new value.
                target_data = new_data if reverse else old_data
                try:
                    setattr(target_data, key, my_value)
                except AttributeError:
                    if prop is not None:
                        msg = "Mergeable read-only properties make no sense! Ignoring: %s.%s"
                        msg %= (cls.__name__, key)
                        warn(msg, stacklevel=4)

        # Overwrite strategy.
        elif overwrite.is_overwriteable_property(prop):

            # Get the resulting value.
            my_value = getattr(old_data, key, None)
            my_value = getattr(new_data, key, my_value)

            # Set the resulting value.
            target_data = new_data if reverse else old_data
            try:
                setattr(target_data, key, my_value)
            except AttributeError:
                msg = "Overwriteable read-only properties make no sense! Ignoring: %s.%s"
                msg %= (cls.__name__, key)
                warn(msg, stacklevel=4)


    @classmethod
    def _merge_links(cls, old_data, new_data, reverse = False):
        """
        Merge links as the union of all links from both objects.

        .. warning: This is an internally used method. Do not call!

        :param old_data: Old data object.
        :type old_data: Data

        :param new_data: New data object.
        :type new_data: Data

        :param reverse: Merge order flag.
        :type reverse: bool
        """
        if reverse:
            for data_type, new_subdict in new_data.__linked.items():
                target_subdict = old_data.__linked[data_type].copy()
                for data_subtype, identity_set in new_subdict.iteritems():
                    target_subdict[data_subtype] = target_subdict[data_subtype].union(identity_set)
                new_data.__linked[data_type] = target_subdict
        else:
            for data_type, new_subdict in new_data.__linked.iteritems():
                my_subdict = old_data.__linked[data_type]
                for data_subtype, identity_set in new_subdict.iteritems():
                    my_subdict[data_subtype].update(identity_set)


    #----------------------------------------------------------------------
    @property
    def links(self):
        """
        :returns: Set of linked Data identities.
        :rtype: set(str)
        """
        return self.__linked[None][None]


    #----------------------------------------------------------------------
    @property
    def linked_data(self):
        """
        :returns: Set of linked Data elements.
        :rtype: set(Data)
        """
        return self._convert_links_to_data( self.__linked[None][None] )


    #----------------------------------------------------------------------
    def get_links(self, data_type = None, data_subtype = None):
        """
        Get the linked Data identities of the given data type.

        :param data_type: Optional data type. One of the Data.TYPE_* values.
        :type data_type: int

        :param data_subtype: Optional data subtype.
        :type data_subtype: int | str

        :returns: Identities.
        :rtype: set(str)

        :raises ValueError: Invalid data_type argument.
        """
        if data_type is None:
            if data_subtype is not None:
                raise NotImplementedError(
                    "Can't filter by subtype for all types")
        return self.__linked[data_type][data_subtype]


    #----------------------------------------------------------------------
    def get_linked_data(self, data_type = None, data_subtype = None):
        """
        Get the linked Data elements of the given data type.

        :param data_type: Optional data type. One of the Data.TYPE_* values.
        :type data_type: int

        :param data_subtype: Optional data subtype.
        :type data_subtype: int | str

        :returns: Data elements.
        :rtype: set(Data)

        :raises ValueError: Invalid data_type argument.
        """
        links = self.get_links(data_type, data_subtype)
        return self._convert_links_to_data(links)


    @staticmethod
    def _convert_links_to_data(links):
        """
        Get the Data objects from a given set of identities.
        This will include both new objects created by this plugins,
        and old objects already stored in the database.

        .. warning: This is an internally used method. Do not call!

        :param links: Set of identities to fetch.
        :type links: set(str)

        :returns: Set of Data objects.
        :rtype: set(Data)
        """
        if not LocalDataCache._enabled:
            return set( Database.get_many(links) )
        remote = set()
        instances = set()
        for ref in links:
            data = LocalDataCache.get(ref)
            if data is None:
                remote.add(ref)
            else:
                instances.add(data)
        if remote:
            instances.update( Database.get_many(remote) )
        return instances


    #----------------------------------------------------------------------
    def add_link(self, other):
        """
        Link two Data instances together.

        :param other: Another instance of Data.
        :type other: Data
        """
        if not isinstance(other, Data):
            raise TypeError("Expected Data, got %s instead" % type(other))
        if self._can_link(other) and other._can_link(self):
            other._add_link(self)
            self._add_link(other)


    def _can_link(self, other):
        """
        Internal method to check if adding a new link
        of the requested type is allowed for this class.

        .. warning: Do not call! Use add_link() instead.

        :param other: Another instance of Data.
        :type other: Data

        :returns: True if permitted, False otherwise.
        :rtype: bool
        """
        max_data = self.max_data
        data_type = other.data_type
        if data_type == self.TYPE_INFORMATION:
            max_data_type = self.max_informations
        elif data_type == self.TYPE_RESOURCE:
            max_data_type = self.max_resources
        elif data_type == self.TYPE_VULNERABILITY:
            max_data_type = self.max_vulnerabilities
        else:
            raise ValueError("Internal error! Unknown data_type: %r" % data_type)
        return (
            (     max_data is None or      max_data < 0 or                len(self.links) <= max_data     ) and
            (max_data_type is None or max_data_type < 0 or len(self.get_links(data_type)) <= max_data_type)
        )


    def _add_link(self, other):
        """
        Internal method to link two Data instances together.

        .. warning: Do not call! Use add_link() instead.

        :param other: Another instance of Data.
        :type other: Data
        """
        data_id = other.identity
        data_type = other.data_type
        self.__linked[None][None].add(data_id)
        self.__linked[data_type][None].add(data_id)
        if data_type == self.TYPE_INFORMATION:
            self.__linked[data_type][other.information_type].add(data_id)
        elif data_type == self.TYPE_RESOURCE:
            self.__linked[data_type][other.resource_type].add(data_id)
        elif data_type == self.TYPE_VULNERABILITY:
            self.__linked[data_type][other.vulnerability_type].add(data_id)
        else:
            raise ValueError("Internal error! Unknown data_type: %r" % data_type)


    #----------------------------------------------------------------------
    def validate_link_minimums(self):
        """
        Validates the link minimum constraints. Raises an exception if not met.

        Note: The maximums are already checked when creating the links.

        This method is called automatically after plugins return the data.
        Plugins do not need to call it.

        :raises ValueError: The minimum link constraints are not met.
        """

        # Check the total link minimum.
        min_data = self.min_data
        if min_data is not None and min_data >= 0:
            found_data = len(self.links)
            if found_data < min_data:
                msg = "Not enough linked Data objects: %d required but %d found"
                raise ValueError(msg % (min_data, found_data))

        # Check the link minimum for each type.
        for data_type, s_type, min_data in (
            (self.TYPE_INFORMATION,   "Information",   self.min_informations),
            (self.TYPE_RESOURCE,      "Resource",      self.min_resources),
            (self.TYPE_VULNERABILITY, "Vulnerability", self.min_vulnerabilities),
        ):
            if min_data is not None and min_data >= 0:
                found_data = len(self.get_links(data_type))
                if found_data < min_data:
                    msg = "Not enough linked %s objects: %d required but %d found"
                    raise ValueError(msg % (s_type, min_data, found_data))


    #----------------------------------------------------------------------
    @property
    def associated_resources(self):
        """
        Get the associated resources.

        :return: Resources.
        :rtype: set(Resource)
        """
        return self.get_linked_data(Data.TYPE_RESOURCE)


    #----------------------------------------------------------------------
    @property
    def associated_informations(self):
        """
        Get the associated informations.

        :return: Informations.
        :rtype: set(Information)
        """
        return self.get_linked_data(Data.TYPE_INFORMATION)


    #----------------------------------------------------------------------
    @property
    def associated_vulnerabilities(self):
        """
        Get the associated vulnerabilities.

        :return: Vulnerabilities.
        :rtype: set(Vulnerability)
        """
        return self.get_linked_data(Data.TYPE_VULNERABILITY)


    #----------------------------------------------------------------------
    def get_associated_vulnerabilities_by_category(self, cat_name = None):
        """
        Get associated vulnerabilites by category.

        :param cat_name: category name
        :type cat_name: str

        :return: Associated vulnerabilites. Returns an empty set if the category doesn't exist.
        :rtype: set(Vulnerability)
        """
        return self.get_linked_data(self.TYPE_VULNERABILITY, cat_name)


    #----------------------------------------------------------------------
    def get_associated_informations_by_category(self, information_type = None):
        """
        Get associated informations by type.

        :param information_type: One of the Information.INFORMATION_* constants.
        :type information_type: int

        :return: Associated informations.
        :rtype: set(Information)

        :raises ValueError: The specified information type is invalid.
        """
        if type(information_type) is not int:
            raise TypeError("Expected int, got %r instead" % type(information_type))
##        if not Information.INFORMATION_FIRST >= information_type >= Information.INFORMATION_LAST:
##            raise ValueError("Invalid information_type: %r" % information_type)
        return self.get_linked_data(self.TYPE_INFORMATION, information_type)


    #----------------------------------------------------------------------
    def get_associated_resources_by_category(self, resource_type = None):
        """
        Get associated informations by type.

        :param resource_type: One of the Resource.RESOURCE_* constants.
        :type resource_type: int

        :return: Associated resources.
        :rtype: set(Resource)

        :raises ValueError: The specified resource type is invalid.
        """
        if type(resource_type) is not int:
            raise TypeError("Expected int, got %r instead" % type(resource_type))
##        if not Resource.RESOURCE_FIRST >= resource_type >= Resource.RESOURCE_LAST:
##            raise ValueError("Invalid resource_type: %r" % resource_type)
        return self.get_linked_data(self.TYPE_RESOURCE, resource_type)


    #----------------------------------------------------------------------
    def add_resource(self, res):
        """
        Associate a resource.

        :param res: Resource element.
        :type res: Resource
        """
        if not hasattr(res, "data_type") or res.data_type != self.TYPE_RESOURCE:
            raise TypeError("Expected Resource, got %s instead" % type(res))
        self.add_link(res)


    #----------------------------------------------------------------------
    def add_information(self, info):
        """
        Associate an information.

        :param info: Information element.
        :type info: Information
        """
        if not hasattr(info, "data_type") or info.data_type != self.TYPE_INFORMATION:
            raise TypeError("Expected Information, got %s instead" % type(info))
        self.add_link(info)


    #----------------------------------------------------------------------
    def add_vulnerability(self, vuln):
        """
        Associate a vulnerability.

        :param info: Vulnerability element.
        :type info: Vulnerability
        """
        if not hasattr(vuln, "data_type") or vuln.data_type != self.TYPE_VULNERABILITY:
            raise TypeError("Expected Vulnerability, got %s instead" % type(vuln))
        self.add_link(vuln)


    #----------------------------------------------------------------------
    @property
    def discovered(self):
        """
        Returns a list with the new Data objects discovered.

        .. warning: This property is used by GoLismero itself.
                    Plugins do not need to access it.

        :return: New resources discovered.
        :rtype: list(Resource)
        """
        return []


    #----------------------------------------------------------------------
    def is_in_scope(self):
        """
        Determines if this Data object is within the scope of the current audit.

        .. warning: This method is used by GoLismero itself.
                    Plugins do not need to call it.

        :return: True if within scope, False otherwise.
        :rtype: bool
        """
        return True


    #----------------------------------------------------------------------
    def __eq__(self, obj):
        """
        Determines equality of Data objects by comparing its identity property.

        :param obj: Data object.
        :type obj: Data

        :return: True if the two Data objects have the same identity, False otherwise.
        :rtype: bool
        """
        # TODO: maybe we should compare all properties, not just identity.
        return self.identity == obj.identity


#----------------------------------------------------------------------
class _LocalDataCache(Singleton):
    """
    Temporary storage for newly created objects.

    .. warning: Used internally by GoLismero, do not use in plugins!
    """


    #----------------------------------------------------------------------
    def __init__(self):
        self.__cleanup()


    #----------------------------------------------------------------------
    def __cleanup(self):
        """
        Reset the internal state.
        """

        # Disable for local plugins.
        try:
            self._enabled = not Config._context.is_local()
        except SyntaxError:
            self._enabled = False   # plugin environment not initialized

        # Map of identities to newly created instances.
        self.__new_data  = {}

        # List of fresh instances, not yet fully initialized.
        self.__fresh     = []

        # Set of ignored data ids.
        self.__discarded = set()

        # Set of autogenerated data ids.
        self.__autogen   = set()


    #----------------------------------------------------------------------
    def on_run(self):
        """
        Called by the plugin bootstrap when a plugin is run.
        """
        self.__cleanup()


    #----------------------------------------------------------------------
    def on_create(self, data):
        """
        Called by instances when being created.

        :param data: New instance, may not yet be fully initialized.
        :type data: Data
        """

        # Check the local data cache is enabled.
        if self._enabled:

            # The list is ordered so newer instances appear last.
            self.__fresh.append(data)


    #----------------------------------------------------------------------
    def update(self):
        """
        Process the fresh instances.
        """

        # Check the local data cache is enabled.
        if not self._enabled:
            return

        # Reset the fresh instances list, but keep the old one apart.
        self.__fresh, fresh = [], self.__fresh

        # This set will contain the data identities.
        new_ids = set()

        # For each new data, from older to newer...
        for data in fresh:

            # Get the identity.
            data_id = data.identity

            # Keep (and overwrite) the data. Order is important!
            # XXX FIXME review, some data may be lost... (merge instead?)
            ##if data_id in self.__new_data:
            ##    warn("Data already existed! %r" % self.__new_data[data_id])
            self.__new_data[data_id] = data

            # Remember the identity.
            new_ids.add(data_id)

        # New instances of previously discarded objects are not discarded.
        self.__discarded.difference_update(new_ids)


    #----------------------------------------------------------------------
    def get(self, data_id):
        """
        Fetch an unsent instance given its identity.
        Returns None if the instance is not found.

        :param data_id: Identity to look for.
        :type data_id: str

        :returns: Data object if found, None otherwise.
        :rtype: Data | None
        """

        # Check the local data cache is enabled.
        if not self._enabled:
            return

        # Process the fresh instances.
        self.update()

        # Fetch the data from the cache.
        return self.__new_data.get(data_id, None)


    #----------------------------------------------------------------------
    def discard(self, data_id):
        """
        Explicitly disables consistency checks for the given data identity,
        and remove the data from the cache if present.

        :param data_id: Identity.
        :type data_id: str
        """

        # Check the local data cache is enabled.
        if self._enabled:

            # Correct a common mistake.
            if isinstance(data_id, Data):
                data_id = data_id.identity

            # Process the fresh instances.
            self.update()

            # Add the ID to the discarded set.
            self.__discarded.add(data_id)

            # Remove the data from the cache.
            self.__new_data.pop(data_id, None)

            # Remove the data from the autogenerated set.
            ##if data_id in self.__autogen:
            ##    self.__autogen.remove(data_id)


    #----------------------------------------------------------------------
    def on_autogeneration(self, data):
        """
        Called by the GoLismero API for autogenerated instances.

        :param data: Recently created instance.
        :type data: Data
        """

        # Check the local data cache is enabled.
        if self._enabled:

            # Process the fresh instances.
            self.update()

            # Add the ID to the autogenerated set.
            self.__autogen.add(data.identity)


    #----------------------------------------------------------------------
    def on_finish(self, result):
        """
        Called by the plugin bootstrap when a plugin finishes running.
        """
        try:

            # Process the fresh instances.
            self.update()

            # No results.
            if result is None:
                result = []

            # Single result.
            if isinstance(result, Data):
                result = [result]

            # Multiple results.
            else:
                result = list(result)
                for data in result:
                    if not isinstance(data, Data):
                        msg = "recv_info() returned an invalid data type: %r"
                        raise TypeError(msg % type(data))

            # If the cache is disabled, do no further processing.
            if not self._enabled:
                return result

            # Do not discard data that's explicitly returned.
            discarded_returned = set()
            for data in result:
                data_id = data.identity
                if data_id in self.__discarded:
                    discarded_returned.add(data_id)
                    self.__discarded.remove(data_id)

            # Warn about discarded data that's explicitly returned.
            if discarded_returned:
                msg = "recv_info() returned discarded data: "
                msg += ", ".join(discarded_returned)
                warn(msg, RuntimeWarning)

            # Merge duplicates.
            graph = {}
            merged = []
            for data in result:
                data.validate_link_minimums() # raises ValueError on bad data
                data_id = data.identity
                old_data = graph.get(data_id, None)
                if old_data is not None:
                    if old_data is not data:
                        old_data.merge(data)
                        merged.append(data)
                else:
                    graph[data_id] = data
            if merged:
                msg = "recv_info() returned duplicated results"
                try:
                    msg += ":\n\t" + "\n\t".join(repr(data) for data in merged)
                except Exception:
                    pass
                warn(msg, RuntimeWarning)

            # Grab missing results.
            #
            # 1. Start with the data returned by the plugin (therefore being
            #    referenced by the plugin).
            # 2. For each data not already visited, see if the links point to
            #    other local data that's not referenced.
            # 3. If we found such data, add it to the results and enqueue the
            #    data it references.
            #
            visited = set()
            missing = []
            discarded_ref = set()
            queue = graph.keys()
            while queue:
                data_id = queue.pop()
                if data_id not in visited:
                    visited.add(data_id)
                    data = self.__new_data.get(data_id, None)
                    if data is not None:
                        if data_id in self.__discarded:
                            discarded_ref.add(data_id)
                        for child_id in data.links:
                            if child_id not in graph:
                                child = self.__new_data.get(child_id, None)
                                if child is not None:
                                    missing.append(child)
                                    graph[child_id] = child
                                    queue.extend(child.links)

            # Warn about data being instanced and referenced but not returned.
            # No warnings for autogenerated data, though.
            if missing:
                msg = ("Data created and referenced by plugin,"
                       " but not returned by recv_info()")
                try:
                    missing_ids = {data.identity for data in missing}
                    missing_ids.difference_update(discarded_ref)
                    missing_ids.difference_update(self.__autogen)
                    if missing_ids:
                        try:
                            msg += ":\n\t" + "\n\t".join(
                                repr(data)
                                for data in missing
                                if data.identity in missing_ids
                            )
                        except Exception:
                            msg += ": " + ", ".join(missing_ids)
                        warn(msg, RuntimeWarning)
                except Exception:
                    warn(msg, RuntimeWarning)

            # Warn about discarded data being referenced.
            if discarded_ref:
                msg = ("Data created and referenced by plugin,"
                       " but marked as discarded: ")
                msg += ", ".join(discarded_ref)
                warn(msg, RuntimeWarning)

            # Warn for data being instanced but not returned nor referenced.
            # Do not warn for discarded nor autogenerated in this case.
            orphan = set(self.__new_data.iterkeys())
            orphan.difference_update(self.__discarded)   # discarded
            orphan.difference_update(graph.iterkeys())   # returned + referenced
            orphan.difference_update(self.__autogen)     # autogenerated
            if orphan:
                msg = ("Data created by plugin, but not referenced"
                       " nor returned by recv_info()")
                try:
                    msg += ":\n\t" + "\n\t".join(
                        repr(self.__new_data[data_id]) for data_id in orphan)
                except Exception:
                    pass
                warn(msg, Warning)

            # Remove discarded elements.
            discarded = self.__discarded.intersection(graph.iterkeys())
            for data_id in discarded:
                del graph[data_id]

            # Remove clusters of data out of scope.
            # Data out of scope but referenced is kept.
            scope_map = [
                (d.is_in_scope(), d.identity, d.links)
                for d in graph.itervalues()
            ]
            out_scope_map = {
                identity: links
                for is_in_scope, identity, links in scope_map
                if not is_in_scope
            }
            if out_scope_map:
                in_scope_map = {
                    identity: links
                    for is_in_scope, identity, links in scope_map
                    if is_in_scope
                }
                ids_to_check = list(out_scope_map.iterkeys())
                ids_to_keep  = set(in_scope_map.iterkeys())
                for links in in_scope_map.itervalues():
                    ids_to_keep.update(links)
                changed = True
                while changed and ids_to_check:
                    changed = False
                    ids_to_check_again = []
                    for identity in ids_to_check:
                        if identity in ids_to_keep:
                            changed = True
                            ids_to_keep.update(out_scope_map[identity])
                        else:
                            ids_to_check_again.append(identity)
                    ids_to_check = ids_to_check_again
                for identity in ids_to_check:
                    try:
                        del graph[identity]
                    except KeyError:
                        pass

            # Warn about data out of scope.
            # No warnings for autogenerated data, though.
            out_of_scope = [
                data
                for data in graph.itervalues()
                if data.identity not in self.__autogen and
                   not data.is_in_scope()
            ]
            if out_of_scope:
                msg = "Data out of scope"
                try:
                    msg += ":\n\t" + "\n\t".join(
                        repr(data) for data in out_of_scope)
                except Exception:
                    pass

            # Return the results.
            return graph.values()

        # Clean up before returning.
        finally:
            self.__cleanup()


#----------------------------------------------------------------------
# Temporary storage for newly created objects.
LocalDataCache = _LocalDataCache()
