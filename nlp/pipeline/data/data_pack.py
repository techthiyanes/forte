""" This class defines the core interchange format, deals with basic operations
such as reading, writing, checking and indexing.
"""
import logging
import itertools
from collections import defaultdict
from typing import Union, Dict, Optional, List, DefaultDict
import numpy as np
from sortedcontainers import SortedList

from nlp.pipeline.data.base_ontology import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Meta:
    """
    Meta information of a datapack.
    """

    def __init__(self, doc_id: str = None):
        self.doc_id = doc_id
        self.process_state = None


class InternalMeta:
    """
    The internal meta information of **one kind of entry** in a datapack.
    """
    def __init__(self):
        self.id_counter = 0
        self.fields_created = dict()
        self.default_component = None


class DataIndex:
    """
    A set of indexes used in a datapack.
    """
    def __init__(self, data_pack):
        self.data_pack: DataPack = data_pack
        # basic indexes
        self.entry_index = defaultdict(Entry)
        self.type_index = defaultdict(set)
        self.component_index = defaultdict(set)
        # other indexes
        self.group_index = defaultdict(set)
        self.link_index: Dict[str, DefaultDict[str, set]] = dict()
        self.coverage_index: Dict[str, DefaultDict[str, set]] = dict()
        # idexing switches
        self.group_index_switch = False
        self.link_index_switch = False
        self.coverage_index_switch: Dict[str, bool] = dict()

    def _in_span(self,
                 inner_entry: Union[str, Entry],
                 span: Span) -> bool:
        """Check whether the ``inner entry`` is within the given span.
        Link entries are considered in a span if both the
        parent and the child are within the span. Group entries are
        considered in a span if all the members are within the span.

        Args:
            inner_entry (str or Entry): An :class:`Entry` object to be checked.
                We will check whether this entry is within ``span``.
            span (Span): A :class:`Span` object to be checked. We will check
                whether the ``inner_entry`` is within this span.
        """

        if isinstance(inner_entry, str):
            inner_entry = self.entry_index.get(inner_entry)

        if isinstance(inner_entry, Annotation):
            inner_begin = inner_entry.span.begin
            inner_end = inner_entry.span.end
        elif isinstance(inner_entry, Link):
            child = self.entry_index.get(inner_entry.child)
            parent = self.entry_index.get(inner_entry.parent)
            inner_begin = min(child.span.begin, parent.span.begin)
            inner_end = max(child.span.end, parent.span.end)
        elif isinstance(inner_entry, Group):
            inner_begin = -1
            inner_end = -1
            for m_id in inner_entry.members:
                mem = self.entry_index.get(m_id)
                if inner_begin == -1:
                    inner_begin = mem.span.begin
                inner_begin = min(inner_begin, mem.span.begin)
                inner_end = max(inner_end, mem.span.end)
        else:
            raise ValueError(
                f"Invalid entry type {type(inner_entry)}. A valid entry "
                f"should be an instance of Annotation, Link, or Group."
            )
        return inner_begin >= span.begin and inner_end <= span.end

    def _have_overlap(self,
                      entry1: Union[Annotation, str],
                      entry2: Union[Annotation, str]) -> bool:
        """Check whether the two annotations have overlap in span.

        Args:
            entry1 (str or Annotation): An :class:`Annotation` object to be
                checked, or the tid of the Annotation.
            entry2 (str or Annotation): Another :class:`Annotation` object to be
                checked, or the tid of the Annotation.
        """
        if isinstance(entry1, str):
            entry1 = self.entry_index.get(entry1)

        if not isinstance(entry1, Annotation):
            raise TypeError(f"'entry1' should be an instance of Annotation,"
                            f" but get {type(entry1)}")

        if isinstance(entry2, str):
            entry2 = self.entry_index.get(entry2)

        if not isinstance(entry2, Annotation):
            raise TypeError(f"'entry2' should be an instance of Annotation,"
                            f" but get {type(entry2)}")

        return not (entry1.span.begin >= entry2.span.end or
                    entry1.span.end <= entry2.span.begin)

    def update_basic_index(self, entries: List[Entry]):
        """Build or update the basic indexes, including (1) :attr:`entry_index`,
        the index from each tid to the corresponding entry;
        (2) :attr:`type_index`, the index from each type to the entries of that
        type; (3) :attr:`component_index`, the index from each component to the
        entries generated by that component.

        Args:
            entries (list): a list of entires to be added into the basic index.
        """
        for entry in entries:
            name = entry.__class__.__name__
            self.entry_index[entry.tid] = entry
            self.type_index[name].add(entry.tid)
            self.component_index[entry.component].add(entry.tid)

    def update_link_index(self, links: List[Link]):
        """Build or update :attr:`link_index`, the index from child and parent
        nodes to links. :attr:`link_index` consists of two sub-indexes:
        "child_index" is the index from child nodes to their corresponding
        links, and "parent_index" is the index from parent nodes to their
        corresponding links.

        Args:
            links (list): a list of links to be added into the index.
        """
        logger.debug("Updating link index")
        self.link_index_switch = True
        if "child_index" not in self.link_index.keys():
            self.link_index["child_index"] = defaultdict(set)
        if "parent_index" not in self.link_index.keys():
            self.link_index["parent_index"] = defaultdict(set)

        for link in links:
            self.link_index["child_index"][link.child].add(link.tid)
            self.link_index["parent_index"][link.parent].add(link.tid)

    def update_group_index(self, groups: List[Group]):
        """Build or update :attr:`group_index`, the index from group members
         to groups.

        Args:
            groups (list): a list of groups to be added into the index.
        """
        logger.debug("Updating group index")
        self.group_index_switch = True
        for group in groups:
            for member in group.members:
                self.group_index[member].add(group.tid)

    def build_coverage_index(self,
                             annotations,
                             links: List[Link] = None,
                             groups: List[Group] = None,
                             outer_type: Optional[type] = None,
                             inner_type: Optional[type] = None):
        # TODO: update index when add entries. how to be better than O(n^2)?
        #   dynamically updating might be very complex and time consuming.
        """
        Index the coverage relationship from annotations of outer_type to
        entries of inner_type, and store in
        ``self.index.coverage_index["outer_type-to-inner_type"]``. An outer
        annotation is considered to (1) cover an inner annotation if inner.begin
        >= outer.begin and inner.end <= outer.end; (2) cover an inner link if it
        covers both the child and parent of the link. (3) cover an inner group
        if it covers all the members of the group.

        Args:
            annotations (list): A list of all the annotations in the datapack.
            links (list, optional): A list of all the links in the datapack.
                Needed if inner_type includes link types and link_index has not
                been built.
            groups (list, optional): A list of all the groups in the datapack.
                Needed if inner_type includes group types and group_index has
                not been built.
            outer_type (str, optional): The type of the outer annotations. If
                `None`, the outer annotations could be all types of
                annotations, and the index name will be
                "Annotation-to-inner_type".
            inner_type (str, optional): The type of the inner entries. If
                `None`, the inner entries could be all types of entries, and the
                index name will be "outer_type-to-Entry".
        """

        # Initialization
        if outer_type is None: outer_type = Annotation
        if inner_type is None: inner_type = Entry
        if not issubclass(outer_type, Annotation):
            raise TypeError(f"'outer_type' must be a subclass of 'Annotation',"
                            f" but get {outer_type}.")
        if not issubclass(inner_type, Entry):
            raise TypeError(f"'inner_type' must be a subclass of Entry,"
                            f" but get {inner_type}.")
        dict_name = outer_type.__name__ + "-to-" + inner_type.__name__
        logger.debug("Building coverage index %s", dict_name)

        # Check whether inner_type includes Link and Group.
        # If yes, build link_index and group_index first.
        if inner_type is Entry or issubclass(inner_type, Link):
            if not self.link_index_switch:
                if links is None:
                    raise ValueError("'links' parameter should be 'None'. "
                                     "Before building coverage index for links"
                                     ", we must build link index first.")
                self.update_link_index(links)
            index_link_as_inner = True
        else:
            index_link_as_inner = False
        if inner_type is Entry or issubclass(inner_type, Group):
            if not self.group_index_switch:
                if groups is None:
                    raise ValueError("'groups' parameter should be 'None'. "
                                     "Before building coverage index for "
                                     "groups, we must build group index first.")
                self.update_group_index(groups)
            index_group_as_inner = True
        else:
            index_group_as_inner = False

        # Build coverage index
        if dict_name not in self.coverage_index.keys():
            self.coverage_index[dict_name] = defaultdict(set)

        def add_covered_entries(outer, stop, step):
            for k in range(i, stop, step):
                inner = annotations[k]
                if self._in_span(inner, outer.span):
                    if isinstance(inner, inner_type):
                        self.coverage_index[dict_name][outer.tid].add(
                            inner.tid)

                    if index_link_as_inner:
                        for link_id in itertools.chain(
                                self.link_index["child_index"][inner.tid],
                                self.link_index["parent_index"][inner.tid]
                        ):
                            link = self.entry_index[link_id]
                            if not isinstance(link, inner_type):
                                continue
                            if self._in_span(link, outer.span):
                                self.coverage_index[dict_name][
                                    outer.tid].add(
                                    link_id)
                    if index_group_as_inner:
                        for group_id in self.group_index[inner.tid]:
                            group = self.entry_index[group_id]
                            if not isinstance(group, inner_type):
                                continue
                            if self._in_span(group, outer.span):
                                self.coverage_index[dict_name][
                                    outer.tid].add(
                                    group_id)
                elif not self._have_overlap(outer, inner):
                    break

        for i in range(len(annotations)):
            if not isinstance(annotations[i], outer_type):
                continue
            add_covered_entries(annotations[i], -1, -1)
            add_covered_entries(annotations[i], len(annotations), 1)
        self.coverage_index_switch[dict_name] = True

    def get_coverage_index(self,
                           outer_type: Optional[type] = None,
                           inner_type: Optional[type] = None) -> Dict[str,set]:
        """
        Return the coverage index that includes the coverage relationship
        between ``outer_type`` and ``inner_type``. Will check the existance
        of coverage indexes from tightest ("outer_type-to-inner_type") to
        loosest ("Annotation-to-Entry"). If not exist, will build the tightest
        coverage index.

        Args:
            outer_type (str, optional): The type of the outer annotations. If
                `None`, the outer annotations could be all types of
                annotations, and the index name will be
                "Annotation-to-inner_type".
            inner_type (str, optional): The type of the inner entries. If
                `None`, the inner entries could be all types of entries, and the
                index name will be "outer_type-to-Entry".
        """
        if outer_type is None:
            outer_name = "Annotation"
        else:
            outer_name = outer_type.__name__
        if inner_type is None:
            inner_name = "Entry"
        else:
            inner_name = inner_type.__name__

        if self.coverage_index_switch.get(outer_name + "-to-" + inner_name):
            return self.coverage_index[outer_name + "-to-" + inner_name]
        if self.coverage_index_switch.get("Annotation" + "-to-" + inner_name):
            return self.coverage_index["Annotation" + "-to-" + inner_name]
        if self.coverage_index_switch.get(outer_name + "-to-" + "Entry"):
            return self.coverage_index[outer_name + "-to-" + "Entry"]
        if self.coverage_index_switch.get("Annotation" + "-to-" + "Entry"):
            return self.coverage_index["Annotation" + "-to-" + "Entry"]

        self.build_coverage_index(self.data_pack.annotations,
                                  self.data_pack.links,
                                  self.data_pack.groups,
                                  outer_type,
                                  inner_type)
        return self.coverage_index[outer_name + "-to-" + inner_name]


class DataPack:
    """
    A :class:`DataPack' contains a piece of natural language text and a
    collection of NLP entries (annotations, links, and groups). The natural
    language text could be a document, paragraph or in any other granularity.

    Args:
        text (str, optional): A piece of natural language text.
        doc_id (str, optional): A universal id of this data pack.
    """

    def __init__(self, text: str = None, doc_id: str = None):
        self.annotations = SortedList()
        self.links: List[Link] = []
        self.groups: List[Group] = []
        self.meta: Meta = Meta(doc_id)
        self.text: str = text

        self.index: DataIndex = DataIndex(self)
        self.internal_metas = defaultdict(InternalMeta)

    def add_entry(self, entry: Entry):
        """
        Try to add an :class:`Entry` object to the :class:`DataPack` object.
        If a same entry already exists, will not add the new one.

        Args:
            entry (Entry): An :class:`Entry` object to be added to the datapack.
            indexing (bool): Whether to update the data pack index. Indexing is
                always suggested unless you are sure that your pipeline will
                never refer it.

        Returns:
            If a same annotation already exists, returns the tid of the
            existing annotation. Otherwise, return the tid of the annotation
            just added.
        """
        if isinstance(entry, Annotation):
            target = self.annotations
        elif isinstance(entry, Link):
            target = self.links
        elif isinstance(entry, Group):
            target = self.groups
        else:
            raise ValueError(
                f"Invalid entry type {type(entry)}. A valid entry "
                f"should be an instance of Annotation, Link, or Group."
            )

        if entry not in target:
            # add the entry to the target entry list
            name = entry.__class__.__name__
            if entry.tid is None:
                entry.set_tid(str(self.internal_metas[name].id_counter))
            entry.data_pack = self
            if isinstance(target, list):
                target.append(entry)
            else:
                target.add(entry)
            self.internal_metas[name].id_counter += 1

            # update the data pack index if needed
            self.index.update_basic_index([entry])
            if self.index.link_index_switch and isinstance(entry, Link):
                self.index.update_link_index([entry])
            if self.index.group_index_switch and isinstance(entry, Group):
                self.index.update_group_index([entry])

            return entry.tid
        # logger.debug(f"Annotation already exist {annotation.tid}")
        return target[target.index(entry)].tid

    def record_fields(self, fields: list, component: str, entry_type: str):
        """Record in the internal meta that ``component`` has generated
        ``fields`` for ``entry_type``.
        """
        if entry_type not in self.internal_metas.keys():
            self.internal_metas[entry_type].default_component = component

        # ensure to record entry_type if fields list is empty
        if component not in self.internal_metas[
            entry_type].fields_created.keys():
            self.internal_metas[entry_type].fields_created[component] = set()
        fields.append("tid")
        for field in fields:
            self.internal_metas[entry_type].fields_created[component].add(field)

    def set_meta(self, **kwargs):
        for k, v in kwargs.items():
            if not hasattr(self.meta, k):
                raise AttributeError(f"Meta has no attribute named {k}")
            setattr(self.meta, k, v)

    def get_data(
            self,
            context_type: str,
            annotation_types: Dict[str, Union[Dict, Iterable]] = None,
            link_types: Dict[str, Union[Dict, Iterable]] = None,
            group_types: Dict[str, Union[Dict, Iterable]] = None,
            offset: int = 0
    ) -> Iterable[Dict]:
        """

        Args:
            context_type (str): The granularity of the data context, which
                could be either `"sentence"` or `"document"`
            annotation_types (dict): The annotation types and fields required.
                The keys of the dict are the required annotation types and the
                values could be a list, set, or tuple of field names. Users can
                also specify the component from which the annotations are
                generated by using dict as value.
            link_types (dict): The link types and fields required.
                The keys of the dict are the required link types and the
                values could be a list, set, or tuple of field names. Users can
                also specify the component from which the annotations are
                generated.
            group_types (dict): The group types and fields required.
                The keys of the dict are the required group types and the
                values could be a list, set, or tuple of field names. Users can
                also specify the component from which the annotations are
                generated.
            offset (int): Will skip the first `offset` instances and generate
                data from the `offset` + 1 instance.
        Returns:
            A data generator, which generates one piece of data (a dict
            containing the required annotations and context).
        """

        if context_type.lower() == "document":
            data = dict()
            data["context"] = self.text
            data["offset"] = 0

            if annotation_types:
                for a_type, a_args in annotation_types.items():
                    data[a_type] = self._generate_annotation_entry_data(
                        a_type, a_args, None
                    )

            if link_types:
                for a_type, a_args in link_types.items():
                    data[a_type] = self._generate_link_entry_data(
                        a_type, a_args, None
                    )
            yield data

        elif context_type.lower() == "sentence":

            sent_meta = self.internal_metas.get("Sentence")
            if sent_meta is None:
                raise AttributeError(
                    f"Document '{self.meta.doc_id}' has no sentence "
                    f"annotations'"
                )

            sent_args = annotation_types.get(
                "Sentence") if annotation_types else None

            sent_component, sent_fields = self._process_request_args(
                "Sentence", sent_args
            )

            valid_sent_ids = (self.index.type_index["Sentence"]
                              & self.index.component_index[sent_component])

            skipped = 0
            for sent in self.annotations:  # to maintain the order
                if sent.tid not in valid_sent_ids:
                    continue
                if skipped < offset:
                    skipped += 1
                    continue

                data = dict()
                data["context"] = self.text[sent.span.begin: sent.span.end]
                data["offset"] = sent.span.begin

                for field in sent_fields:
                    if field not in sent_meta.fields_created[sent_component]:
                        raise AttributeError(
                            f"Sentence annotation generated by "
                            f"'{sent_component}' has no field named '{field}'."
                        )

                    data[field] = getattr(sent, field)

                if annotation_types is not None:
                    for a_type, a_args in annotation_types.items():
                        if a_type == "Sentence":
                            continue

                        data[a_type] = self._generate_annotation_entry_data(
                            a_type, a_args, sent
                        )
                if link_types is not None:
                    for a_type, a_args in link_types.items():
                        data[a_type] = self._generate_link_entry_data(
                            a_type, a_args, sent
                        )

                if group_types is not None:
                    for a_type, a_args in group_types.items():
                        pass

                yield data

    def _process_request_args(self, a_type, a_args):

        # check the existence of ``a_type`` annotation in ``doc``
        a_meta = self.internal_metas.get(a_type)
        if a_meta is None:
            raise AttributeError(
                f"Document '{self.meta.doc_id}' has no '{a_type}' "
                f"annotations'"
            )

        # request which fields generated by which component
        component = None
        fields = {}
        if isinstance(a_args, dict):
            component = a_args.get("component")
            a_args = a_args.get("fields", {})

        if isinstance(a_args, Iterable):
            fields = set(a_args)
        elif a_args is not None:
            raise TypeError(
                f"Invalid request for '{a_type}'. "
                f"The request should be of an iterable type or a dict."
            )

        if component is None:
            component = a_meta.default_component

        if component not in a_meta.fields_created.keys():
            raise AttributeError(
                f"DataPack has no {a_type} annotations generated"
                f" by {component}"
            )

        return component, fields

    def _generate_annotation_entry_data(
            self,
            a_type: str,
            a_args: Union[Dict, List],
            sent: Optional[BaseOntology.Sentence]) -> Dict:

        component, fields = self._process_request_args(a_type, a_args)

        a_dict = dict()

        a_dict["span"] = []
        a_dict["text"] = []
        for field in fields:
            a_dict[field] = []

        sent_begin = sent.span.begin if sent else 0
        sent_end = sent.span.end if sent else self.annotations[-1].span.end

        # ``a_type`` annotations generated by ``component`` in this ``sent``
        if sent:
            valid_id = (self.index.coverage_index["Sentence-to-Entry"][sent.tid]
                        & self.index.type_index[a_type]
                        & self.index.component_index[component])
        else:
            valid_id = (self.index.type_index[a_type]
                        & self.index.component_index[component])

        begin_index = self.annotations.bisect(Annotation('', sent_begin, -1))
        end_index = self.annotations.bisect(Annotation('', sent_end, -1))

        for annotation in self.annotations[begin_index: end_index]:
            if annotation.tid not in valid_id:
                continue

            a_dict["span"].append((annotation.span.begin - sent_begin,
                                   annotation.span.end - sent_begin))
            a_dict["text"].append(self.text[annotation.span.begin:
                                            annotation.span.end])
            for field in fields:
                if field not in self.internal_metas[a_type].fields_created[
                    component
                ]:
                    raise AttributeError(
                        f"'{a_type}' annotation generated by "
                        f"'{component}' has no field named '{field}'"
                    )
                a_dict[field].append(getattr(annotation, field))

        for key, value in a_dict.items():
            a_dict[key] = np.array(value)

        return a_dict

    def _generate_link_entry_data(
            self,
            a_type: str,
            a_args: Union[Dict, List],
            sent: Optional[BaseOntology.Sentence]) -> Dict:

        component, fields = self._process_request_args(a_type, a_args)

        parent_fields = {f for f in fields if f.split('.')[0] == "parent"}
        child_fields = {f for f in fields if f.split('.')[0] == "child"}

        a_dict = dict()
        for field in fields:
            a_dict[field] = []
        if parent_fields:
            a_dict["parent.span"] = []
            a_dict["parent.text"] = []
        if child_fields:
            a_dict["child.span"] = []
            a_dict["child.text"] = []

        sent_begin = sent.span.begin if sent else 0

        # ``a_type`` annotations generated by ``component`` in this ``sent``
        if sent:
            valid_id = (self.index.coverage_index["Sentence-to-Entry"][sent.tid]
                        & self.index.type_index[a_type]
                        & self.index.component_index[component])
        else:
            valid_id = (self.index.type_index[a_type]
                        & self.index.component_index[component])

        for link_id in valid_id:
            link = self.index.entry_index[link_id]
            if not isinstance(link, Link):
                raise TypeError(f"expect Link object, but get {type(link)}")

            if parent_fields:
                p_id = link.parent
                parent = self.index.entry_index[p_id]
                if not isinstance(parent, Annotation):
                    raise TypeError(f"'parent'' should be an Annotation object "
                                    f"but got {type(parent)}.")
                p_type = parent.__class__.__name__
                a_dict["parent.span"].append((parent.span.begin - sent_begin,
                                              parent.span.end - sent_begin,))
                a_dict["parent.text"].append(self.text[parent.span.begin:
                                                       parent.span.end])
                for field in parent_fields:
                    p_field = field.split(".")
                    if len(p_field) == 1:
                        continue
                    if len(p_field) > 2:
                        raise AttributeError(
                            f"Too many delimiters in field name {field}."
                        )
                    p_field = p_field[1]

                    if p_field not in \
                            self.internal_metas[p_type].fields_created[
                                parent.component
                            ]:
                        raise AttributeError(
                            f"'{p_type}' annotation generated by "
                            f"'{parent.component}' has no field named "
                            f"'{p_field}'."
                        )
                    a_dict[field].append(getattr(parent, p_field))

            if child_fields:
                c_id = link.child
                child = self.index.entry_index[c_id]
                if not isinstance(child, Annotation):
                    raise TypeError(f"'parent'' should be an Annotation object "
                                    f"but got {type(child)}.")
                c_type = child.__class__.__name__
                a_dict["child.span"].append((child.span.begin - sent_begin,
                                             child.span.end - sent_begin))
                a_dict["child.text"].append(self.text[child.span.begin:
                                                      child.span.end])
                for field in child_fields:
                    c_field = field.split(".")
                    if len(c_field) == 1:
                        continue
                    if len(c_field) > 2:
                        raise AttributeError(
                            f"Too many delimiters in field name {field}."
                        )
                    c_field = c_field[1]

                    if c_field not in \
                            self.internal_metas[c_type].fields_created[
                                child.component
                            ]:
                        raise AttributeError(
                            f"'{c_type}' annotation generated by "
                            f"'{child.component}' has no field named "
                            f"'{c_field}'."
                        )
                    a_dict[field].append(getattr(child, c_field))

            for field in fields - parent_fields - child_fields:
                if field not in self.internal_metas[a_type].fields_created[
                    component
                ]:
                    raise AttributeError(
                        f"'{a_type}' annotation generated by "
                        f"'{component}' has no field named '{field}'"
                    )
                a_dict[field].append(getattr(link, field))

        for key, value in a_dict.items():
            a_dict[key] = np.array(value)
        return a_dict

    def get_data_batch(
            self,
            batch_size: int,
            context_type: str,
            annotation_types: Dict[str, Union[Dict, Iterable]] = None,
            link_types: Dict[str, Union[Dict, Iterable]] = None,
            group_types: Dict[str, Union[Dict, Iterable]] = None,
            offset: int = 0) -> Iterable[Dict]:

        batch = {}
        cnt = 0
        for data in self.get_data(context_type, annotation_types, link_types,
                                  group_types, offset):
            for entry, fields in data.items():
                if isinstance(fields, dict):
                    if entry not in batch.keys():
                        batch[entry] = {}
                    for k, value in fields.items():
                        if k not in batch[entry].keys():
                            batch[entry][k] = []
                        batch[entry][k].append(value)
                else:  # context level feature
                    if entry not in batch.keys():
                        batch[entry] = []
                    batch[entry].append(fields)
            cnt += 1
            if cnt == batch_size:
                yield (batch, cnt)
                cnt = 0
                batch = {}

        if batch:
            yield (batch, cnt)

    def get_entries(self,
                    entry_type: type,
                    range_annotation: Annotation = None,
                    component: str = None) -> Iterable:
        """
        Get ``entry_type`` entries from the span of ``range_annotation`` of
        DataPack.

        Args:
            entry_type (type): The type of entries requested.
            range_annotation (Annotation, optional): The range of entries
                requested. If `None`, will return valid entries in the range of
                whole data_pack.
            component (str, optional): The component generating the entries
                requested. If `None`, will return valid entries generated by
                any component.
        """
        sent_begin = range_annotation.span.begin if range_annotation else 0
        sent_end = (range_annotation.span.end if range_annotation else
                    self.annotations[-1].span.end)

        # ``a_type`` annotations generated by ``component`` in ``range``
        valid_id = self.index.type_index[entry_type.__name__]
        if component:
            valid_id = valid_id & self.index.component_index[component]
        if range_annotation:
            c_index = self.index.get_coverage_index(type(range_annotation),
                                                    entry_type)
            valid_id = valid_id & c_index[range_annotation.tid]

        if issubclass(entry_type, Annotation):
            begin_index = self.annotations.bisect(Annotation('',sent_begin, -1))
            end_index = self.annotations.bisect(Annotation('', sent_end, -1))
            for annotation in self.annotations[begin_index: end_index]:
                if annotation.tid not in valid_id:
                    continue
                else:
                    yield annotation

        elif issubclass(entry_type, (Link, Group)):
            for entry_id in valid_id:
                entry = self.index.entry_index[entry_id]
                yield entry

