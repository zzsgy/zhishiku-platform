class VersionConflict(ValueError):
    pass


EDITABLE_FIELDS = ('title', 'content_md', 'category', 'tags', 'node_type', 'parent_id')


def apply_snapshot(node, snapshot):
    for field in EDITABLE_FIELDS:
        if field in snapshot:
            setattr(node, field, snapshot[field])
    node.full_clean()
    node.save()
    from .services import auto_link_edges, sync_node_to_file
    from django.db import transaction
    auto_link_edges(node)
    transaction.on_commit(lambda: sync_node_to_file(node))
    return node
