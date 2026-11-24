from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("pos.register", "POS registers"),
    PermissionDef("pos.shift.view", "View POS shifts"),
    PermissionDef("pos.shift.open", "Open a POS shift"),
    PermissionDef("pos.shift.close", "Close a POS shift"),
    PermissionDef("pos.sale.view", "View POS sales"),
    PermissionDef("pos.sale.create", "Sell at the POS"),
    PermissionDef("pos.sale.void", "Void a POS sale (refund)"),
    *crud("pos.coupon", "coupons"),
    PermissionDef("pos.gift_card.view", "View gift cards"),
    PermissionDef("pos.gift_card.create", "Sell and switch off gift cards"),
]
