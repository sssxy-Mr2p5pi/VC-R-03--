"""Teaching-fixture actor/authorization config. Real accounts and management chain wait for P-04."""
import json
from pathlib import Path

from vcr03 import ROOT

CONFIG_PATH = ROOT / 'config' / 'actors.json'

PERMISSIONS = ('DISPATCH', 'REVIEW', 'APPROVE_RELEASE', 'APPROVE_REVERSAL')


def load(path=None):
    """Load the actor document; tests may point at their own inline fixture file."""
    target = Path(path) if path else CONFIG_PATH
    with open(target, encoding='utf-8') as handle:
        return json.load(handle)


def get_actor(actors, actor_id):
    for item in actors['actors']:
        if item['id'] == actor_id:
            return item
    return None


def has_permission(actors, actor_id, permission):
    item = get_actor(actors, actor_id)
    return bool(item and permission in item.get('permissions', []))


def supervisor_of(actors, actor_id):
    item = get_actor(actors, actor_id)
    return item.get('supervisor') if item else None


def chain_up(actors, actor_id):
    """Yield the configured management chain above the actor, nearest first, cycle-safe."""
    seen = {actor_id}
    current = supervisor_of(actors, actor_id)
    while current and current not in seen:
        seen.add(current)
        yield current
        current = supervisor_of(actors, current)


def qualified(actors, permission, exclude=()):
    """Ids holding the permission, minus excluded ids; used for avoidance/escalation checks."""
    excluded = set(exclude)
    return [a['id'] for a in actors['actors']
            if permission in a.get('permissions', []) and a['id'] not in excluded]
