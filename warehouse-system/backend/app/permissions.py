"""Fixed role-to-operation mapping. Every active user may use read endpoints."""

ROLE_PERMISSIONS = {
    'admin': {'*'},
    'warehouse': {
        'material.archive.write', 'material.inventory.write',
        'finished.archive.write', 'finished.inventory.write',
        'supplier.write', 'location.write',
    },
    'workshop_director': {
        'bom.write', 'production.order.write', 'production.manage',
        'workshop.pick', 'workshop.replenish', 'workshop.return',
    },
    'workshop': {'workshop.pick', 'workshop.replenish', 'workshop.return'},
    'accountant': {
        'material.archive.write', 'finished.archive.write',
        'bom.write', 'production.order.write', 'supplier.write',
    },
}


def can_operate(role_code: str, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(role_code, set())
    return '*' in granted or permission in granted
