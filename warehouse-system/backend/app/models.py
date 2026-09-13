"""第一版业务模型。库存写入接口将在后续阶段通过事务服务实现。"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime

QTY = Numeric(20, 6)


def utcnow():
    return datetime.now(timezone.utc)


class Identity:
    id: Mapped[int] = mapped_column(primary_key=True)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Archive:
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Role(Identity, Base):
    __tablename__ = 'roles'
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(40))


class User(Identity, Timestamps, Archive, Base):
    __tablename__ = 'users'
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey('roles.id'), index=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Unit(Base):
    __tablename__ = 'units'
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(20))
    # 质量以 g、体积以 mL 为参考单位；计数单位不能任意互转。
    factor: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    __table_args__ = (CheckConstraint('factor > 0', name='positive_factor'),)


class Supplier(Identity, Timestamps, Archive, Base):
    __tablename__ = 'suppliers'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    contact: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)


class Location(Identity, Timestamps, Archive, Base):
    __tablename__ = 'locations'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class RawMaterial(Identity, Timestamps, Archive, Base):
    __tablename__ = 'raw_materials'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    specification: Mapped[str | None] = mapped_column(String(200))
    min_stock: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('min_stock >= 0', name='nonnegative_min_stock'),)


class FinishedProduct(Identity, Timestamps, Archive, Base):
    __tablename__ = 'finished_products'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'), default='盒')
    specification: Mapped[str | None] = mapped_column(String(200))
    min_stock: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('min_stock >= 0', name='nonnegative_min_stock'),)


class MaterialBatch(Identity, Timestamps, Base):
    __tablename__ = 'material_batches'
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'))
    batch_no: Mapped[str] = mapped_column(String(80))
    received_quantity: Mapped[Decimal] = mapped_column(QTY)
    remaining_quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    production_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[int] = mapped_column(ForeignKey('suppliers.id'), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey('locations.id'), index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('material_id', 'batch_no', 'supplier_id', 'location_id'),
        Index('ix_material_batches_fefo', 'material_id', 'expiry_date', 'received_at'),
        Index('ix_material_batches_expiry', 'expiry_date'),
        CheckConstraint('received_quantity > 0 AND remaining_quantity >= 0 AND remaining_quantity <= received_quantity', name='valid_quantities'),
        CheckConstraint('expiry_date >= production_date', name='valid_dates'),
    )


class BomHeader(Identity, Timestamps, Archive, Base):
    __tablename__ = 'bom_headers'
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='draft')
    output_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('product_id', 'version'),
                     CheckConstraint('version > 0', name='positive_version'),
                     CheckConstraint("status IN ('draft','active','archived')", name='valid_status'))


class BomItem(Identity, Base):
    __tablename__ = 'bom_items'
    bom_id: Mapped[int] = mapped_column(ForeignKey('bom_headers.id'), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'), index=True)
    quantity_per_unit: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    __table_args__ = (UniqueConstraint('bom_id', 'material_id'), CheckConstraint('quantity_per_unit > 0', name='positive_quantity'))


class ProductionOrder(Identity, Timestamps, Base):
    __tablename__ = 'production_orders'
    order_no: Mapped[str] = mapped_column(String(48), unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey('bom_headers.id'))
    planned_quantity: Mapped[Decimal] = mapped_column(QTY)
    actual_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    status: Mapped[str] = mapped_column(String(20), default='draft', index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint('planned_quantity > 0 AND (actual_quantity IS NULL OR actual_quantity >= 0)', name='valid_quantities'),
        CheckConstraint("status IN ('draft','released','in_progress','completed','received','cancelled')", name='valid_status'),
    )


class ProductionOrderMaterial(Identity, Base):
    __tablename__ = 'production_order_materials'
    production_order_id: Mapped[int] = mapped_column(ForeignKey('production_orders.id'), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'), index=True)
    # 快照已转换为原料库存单位；以后更改 BOM 不影响历史单据。
    quantity_per_unit: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    planned_theoretical_quantity: Mapped[Decimal] = mapped_column(QTY)
    final_theoretical_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    __table_args__ = (
        UniqueConstraint('production_order_id', 'material_id'),
        CheckConstraint('quantity_per_unit > 0 AND planned_theoretical_quantity > 0 AND (final_theoretical_quantity IS NULL OR final_theoretical_quantity >= 0)', name='valid_quantities'),
    )


class StockDocument(Identity, Base):
    """每次库存操作的统一单据；请求标识唯一，防止双击重复记账。"""
    __tablename__ = 'stock_documents'
    document_no: Mapped[str] = mapped_column(String(48), unique=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("kind IN ('material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound')", name='valid_kind'),)


class MaterialMovement(Identity, Base):
    __tablename__ = 'material_movements'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    input_quantity: Mapped[Decimal] = mapped_column(QTY)
    input_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    __table_args__ = (CheckConstraint('quantity > 0 AND input_quantity > 0', name='positive_quantities'),)


class MaterialIssueRecord(Identity, Base):
    __tablename__ = 'material_issue_records'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    order_material_id: Mapped[int] = mapped_column(ForeignKey('production_order_materials.id'), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    input_quantity: Mapped[Decimal] = mapped_column(QTY)
    input_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    __table_args__ = (CheckConstraint("kind IN ('issue','supplement','return')", name='valid_kind'),
                     CheckConstraint('quantity > 0 AND input_quantity > 0', name='positive_quantities'))


class MaterialRequest(Identity, Timestamps, Base):
    """车间提需求，仓库确认后才产生实际库存记录。"""
    __tablename__ = 'material_requests'
    order_material_id: Mapped[int] = mapped_column(ForeignKey('production_order_materials.id'), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    fulfilled_document_id: Mapped[int | None] = mapped_column(ForeignKey('stock_documents.id'), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('quantity > 0', name='positive_quantity'),
                     CheckConstraint("kind IN ('issue','supplement','return')", name='valid_kind'),
                     CheckConstraint("status IN ('pending','fulfilled','cancelled')", name='valid_status'))


class FinishedBatch(Identity, Timestamps, Base):
    __tablename__ = 'finished_batches'
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    batch_no: Mapped[str] = mapped_column(String(80))
    location_id: Mapped[int] = mapped_column(ForeignKey('locations.id'))
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    remaining_quantity: Mapped[Decimal] = mapped_column(QTY)
    production_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint('product_id', 'batch_no', 'location_id'),
                     CheckConstraint('remaining_quantity >= 0', name='nonnegative_stock'),
                     CheckConstraint('expiry_date IS NULL OR production_date IS NULL OR expiry_date >= production_date', name='valid_dates'))


class FinishedReceipt(Identity, Base):
    __tablename__ = 'finished_receipts'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), unique=True)
    production_order_id: Mapped[int | None] = mapped_column(ForeignKey('production_orders.id'), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('finished_batches.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (CheckConstraint('quantity > 0', name='positive_quantity'),)


class FinishedOutbound(Identity, Base):
    __tablename__ = 'finished_outbounds'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('finished_batches.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (CheckConstraint('quantity > 0', name='positive_quantity'),)


class InventoryTransaction(Identity, Base):
    __tablename__ = 'inventory_transactions'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    material_batch_id: Mapped[int | None] = mapped_column(ForeignKey('material_batches.id'), index=True)
    finished_batch_id: Mapped[int | None] = mapped_column(ForeignKey('finished_batches.id'), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    before_quantity: Mapped[Decimal] = mapped_column(QTY)
    change_quantity: Mapped[Decimal] = mapped_column(QTY)
    after_quantity: Mapped[Decimal] = mapped_column(QTY)
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint('(material_batch_id IS NOT NULL AND finished_batch_id IS NULL) OR (material_batch_id IS NULL AND finished_batch_id IS NOT NULL)', name='one_batch_only'),
        CheckConstraint('before_quantity >= 0 AND after_quantity >= 0 AND change_quantity <> 0 AND before_quantity + change_quantity = after_quantity', name='balanced_quantities'),
        CheckConstraint("kind IN ('material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound')", name='valid_kind'),
    )


class WarningRule(Identity, Timestamps, Base):
    __tablename__ = 'warning_rules'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    days: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint('days >= 0', name='nonnegative_days'),)


class SystemSetting(Base):
    __tablename__ = 'system_settings'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class OperationLog(Identity, Base):
    __tablename__ = 'operation_logs'
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
