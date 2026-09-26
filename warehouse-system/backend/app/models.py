"""第一版业务模型。库存由事务服务记账，所有变动伴随流水。"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Computed, Date, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
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
    # 原材料档案只保存计量单位和最低库存阈值；实时库存必须由批次余额汇总，不能写回本表。
    __tablename__ = 'raw_materials'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    specification: Mapped[str | None] = mapped_column(String(200))
    min_stock: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('min_stock >= 0', name='nonnegative_min_stock'),)


class FinishedProduct(Identity, Timestamps, Archive, Base):
    # 成品单位参与 BOM、生产单和库存记账，业务发生后不能被随意改写；“盒”只是默认值。
    __tablename__ = 'finished_products'
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'), default='盒')
    specification: Mapped[str | None] = mapped_column(String(200))
    min_stock: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('min_stock >= 0', name='nonnegative_min_stock'),)


class MaterialBatch(Identity, Timestamps, Base):
    # 库位批次承载可用库存；location_id 为空的行是尚未上架的入库暂存批次。
    __tablename__ = 'material_batches'
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'))
    batch_no: Mapped[str] = mapped_column(String(80))
    received_quantity: Mapped[Decimal] = mapped_column(QTY)
    remaining_quantity: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    production_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[int] = mapped_column(ForeignKey('suppliers.id'), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey('locations.id'), index=True)
    stock_status: Mapped[str] = mapped_column(String(16), default='shelved')
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('material_id', 'batch_no', 'supplier_id', 'location_id'),
        Index('ix_material_batches_fefo', 'material_id', 'expiry_date', 'received_at'),
        Index('ix_material_batches_expiry', 'expiry_date'),
        CheckConstraint('received_quantity > 0 AND remaining_quantity >= 0 AND remaining_quantity <= received_quantity', name='valid_quantities'),
        CheckConstraint("(stock_status = 'pending' AND location_id IS NULL) OR (stock_status = 'shelved' AND location_id IS NOT NULL)", name='valid_stock_status'),
        CheckConstraint('expiry_date >= production_date', name='valid_dates'),
    )


class BomHeader(Identity, Timestamps, Archive, Base):
    # BOM 头保存成品、版本和状态；具体的“每一成品单位用料”由 BomItem 表达。
    __tablename__ = 'bom_headers'
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='draft')
    output_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    notes: Mapped[str | None] = mapped_column(Text)
    # MySQL 唯一索引允许多个 NULL；生成列只在 active 时取成品 ID，数据库层保证并发启用也不会出现双 ACTIVE。
    active_product_id: Mapped[int | None] = mapped_column(
        Integer, Computed("CASE WHEN status = 'active' THEN product_id ELSE NULL END", persisted=True)
    )
    __table_args__ = (UniqueConstraint('product_id', 'version'),
                     UniqueConstraint('active_product_id', name='uq_bom_headers_active_product'),
                     CheckConstraint('version > 0', name='positive_version'),
                     CheckConstraint("status IN ('draft','active','inactive','archived')", name='valid_status'))


class BomItem(Identity, Timestamps, Base):
    # quantity_per_unit 使用本行 unit，生产单创建时会同时保存该原始配方单位和换算后的库存单位数量。
    __tablename__ = 'bom_items'
    bom_id: Mapped[int] = mapped_column(ForeignKey('bom_headers.id'), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'), index=True)
    quantity_per_unit: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('bom_id', 'material_id'), CheckConstraint('quantity_per_unit > 0', name='positive_quantity'))


class ProductionOrder(Identity, Timestamps, Base):
    # 生产单保存成品名称、BOM 版本和单位快照，保证之后修改档案或配方不会改写历史生产事实。
    __tablename__ = 'production_orders'
    order_no: Mapped[str] = mapped_column(String(48), unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(120))
    bom_id: Mapped[int] = mapped_column(ForeignKey('bom_headers.id'))
    bom_version: Mapped[int] = mapped_column(Integer)
    planned_quantity: Mapped[Decimal] = mapped_column(QTY)
    actual_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    status: Mapped[str] = mapped_column(String(20), default='ready', index=True)
    planned_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint('planned_quantity > 0 AND (actual_quantity IS NULL OR actual_quantity >= 0)', name='valid_quantities'),
        CheckConstraint("status IN ('draft','ready','in_progress','completed','received','cancelled')", name='valid_status'),
    )


class ProductionOrderMaterial(Identity, Timestamps, Base):
    # 每行是生产单创建时固化的一种原料快照，也是领料、补料、退料汇总及最终损耗结算的载体。
    __tablename__ = 'production_order_materials'
    production_order_id: Mapped[int] = mapped_column(ForeignKey('production_orders.id'), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey('raw_materials.id'), index=True)
    material_code_snapshot: Mapped[str] = mapped_column(String(40))
    material_name_snapshot: Mapped[str] = mapped_column(String(120))
    # 单位用量保留 BOM 原单位；计划理论量及领补退汇总统一为原料库存单位。
    quantity_per_unit: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    planned_theoretical_quantity: Mapped[Decimal] = mapped_column(QTY)
    base_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    issued_quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    supplemented_quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    returned_quantity: Mapped[Decimal] = mapped_column(QTY, default=Decimal('0'))
    final_theoretical_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    actual_used_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    loss_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    loss_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    __table_args__ = (
        UniqueConstraint('production_order_id', 'material_id'),
        CheckConstraint('quantity_per_unit > 0 AND planned_theoretical_quantity > 0 '
                        'AND issued_quantity >= 0 AND supplemented_quantity >= 0 AND returned_quantity >= 0 '
                        'AND returned_quantity <= issued_quantity + supplemented_quantity '
                        'AND (final_theoretical_quantity IS NULL OR final_theoretical_quantity >= 0)', name='valid_quantities'),
    )


class StockDocument(Identity, Base):
    """每次库存操作的统一单据；请求标识唯一，防止双击重复记账。"""
    __tablename__ = 'stock_documents'
    document_no: Mapped[str] = mapped_column(String(48), unique=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    business_date: Mapped[date | None] = mapped_column(Date)
    business_type: Mapped[str | None] = mapped_column(String(20))
    reference_no: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    reverses_document_id: Mapped[int | None] = mapped_column(Integer)
    reverses_kind: Mapped[str | None] = mapped_column(String(32))
    original_document_no_snapshot: Mapped[str | None] = mapped_column(String(48))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('id', 'kind', name='uq_stock_documents_id_kind'),
        UniqueConstraint('reverses_document_id', name='uq_stock_documents_reverses_document_id'),
        ForeignKeyConstraint(['reverses_document_id', 'reverses_kind'],
                             ['stock_documents.id', 'stock_documents.kind'], name='fk_stock_documents_original_kind'),
        CheckConstraint("kind IN ('material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound','putaway','transfer','count_gain','count_loss','count_match','adjustment','reversal')", name='valid_kind'),
        CheckConstraint("business_type IS NULL OR business_type IN ('normal','production')", name='valid_business_type'),
        CheckConstraint("(kind <> 'reversal' AND reverses_document_id IS NULL AND reverses_kind IS NULL "
                        "AND original_document_no_snapshot IS NULL AND reversal_reason IS NULL) OR "
                        "(kind = 'reversal' AND reverses_document_id IS NOT NULL AND reverses_kind IS NOT NULL "
                        "AND reverses_kind IN ('material_outbound','finished_outbound') "
                        "AND original_document_no_snapshot IS NOT NULL "
                        "AND reversal_reason IS NOT NULL AND CHAR_LENGTH(TRIM(reversal_reason)) > 0)",
                        name='valid_reversal'),
    )


class MaterialMovement(Identity, Base):
    # 普通原料入/出库的单据明细；quantity 已统一为库存单位，input_* 保留操作员原始输入。
    __tablename__ = 'material_movements'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    input_quantity: Mapped[Decimal] = mapped_column(QTY)
    input_unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    shelved_quantity: Mapped[Decimal | None] = mapped_column(QTY)
    __table_args__ = (CheckConstraint('quantity > 0 AND input_quantity > 0', name='positive_quantities'),
                     CheckConstraint('shelved_quantity IS NULL OR (shelved_quantity >= 0 AND shelved_quantity <= quantity)', name='valid_shelved_quantity'))


class MaterialTransfer(Identity, Base):
    __tablename__ = 'material_transfers'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), unique=True)
    source_batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'))
    target_batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'))
    receipt_movement_id: Mapped[int | None] = mapped_column(ForeignKey('material_movements.id'))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    __table_args__ = (CheckConstraint('quantity > 0', name='positive_quantity'),)


class MaterialCount(Identity, Base):
    __tablename__ = 'material_counts'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'))
    book_quantity: Mapped[Decimal] = mapped_column(QTY)
    actual_quantity: Mapped[Decimal] = mapped_column(QTY)
    difference: Mapped[Decimal] = mapped_column(QTY)
    counted_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint('book_quantity >= 0 AND actual_quantity >= 0 AND difference = actual_quantity - book_quantity', name='valid_count'),)


class MaterialAdjustment(Identity, Base):
    __tablename__ = 'material_adjustments'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('material_batches.id'))
    before_quantity: Mapped[Decimal] = mapped_column(QTY)
    change_quantity: Mapped[Decimal] = mapped_column(QTY)
    after_quantity: Mapped[Decimal] = mapped_column(QTY)
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    reason: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint('before_quantity >= 0 AND after_quantity >= 0 AND change_quantity <> 0 AND after_quantity = before_quantity + change_quantity', name='valid_adjustment'),)


class MaterialIssueRecord(Identity, Base):
    # 生产领料、补料、退料的逐批次事实明细，不能只用 ProductionOrderMaterial 上的汇总字段替代。
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
    # 第一版不向用户暴露成品批次：每个成品使用一条 aggregate 行承载普通入出库和生产入库余额。
    __tablename__ = 'finished_batches'
    product_id: Mapped[int] = mapped_column(ForeignKey('finished_products.id'), index=True)
    batch_no: Mapped[str] = mapped_column(String(80))
    location_id: Mapped[int | None] = mapped_column(ForeignKey('locations.id'))
    # NULL 保留原批次结构；aggregate 为当前每成品唯一的汇总库存，页面不暴露内部批号。
    stock_scope: Mapped[str | None] = mapped_column(String(16))
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    remaining_quantity: Mapped[Decimal] = mapped_column(QTY)
    production_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint('product_id', 'batch_no', 'location_id'),
                     UniqueConstraint('product_id', 'stock_scope', name='uq_finished_batches_product_scope'),
                     CheckConstraint("(stock_scope IS NULL AND location_id IS NOT NULL) OR "
                                     "(stock_scope IS NOT NULL AND stock_scope = 'aggregate' AND location_id IS NULL AND batch_no = '__aggregate__' "
                                     "AND production_date IS NULL AND expiry_date IS NULL)", name='valid_scope'),
                     CheckConstraint('remaining_quantity >= 0', name='nonnegative_stock'),
                     CheckConstraint('expiry_date IS NULL OR production_date IS NULL OR expiry_date >= production_date', name='valid_dates'))


class FinishedReceipt(Identity, Base):
    # production_order_id 唯一，数据库层防止同一生产单被重复完工入库。
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
    # 原料与成品共用的不可随意修改库存流水；前量、变动量、后量必须形成可复核的恒等式。
    __tablename__ = 'inventory_transactions'
    document_id: Mapped[int] = mapped_column(ForeignKey('stock_documents.id'), index=True)
    reverses_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey('inventory_transactions.id', name='fk_inventory_transactions_reverses_transaction_id'))
    material_batch_id: Mapped[int | None] = mapped_column(ForeignKey('material_batches.id'), index=True)
    finished_batch_id: Mapped[int | None] = mapped_column(ForeignKey('finished_batches.id'), index=True)
    source_location_id: Mapped[int | None] = mapped_column(ForeignKey('locations.id'))
    target_location_id: Mapped[int | None] = mapped_column(ForeignKey('locations.id'))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    unit: Mapped[str] = mapped_column(ForeignKey('units.code'))
    before_quantity: Mapped[Decimal] = mapped_column(QTY)
    change_quantity: Mapped[Decimal] = mapped_column(QTY)
    after_quantity: Mapped[Decimal] = mapped_column(QTY)
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('reverses_transaction_id', name='uq_inventory_transactions_reverses_transaction_id'),
        CheckConstraint('(material_batch_id IS NOT NULL AND finished_batch_id IS NULL) OR (material_batch_id IS NULL AND finished_batch_id IS NOT NULL)', name='one_batch_only'),
        CheckConstraint('before_quantity >= 0 AND after_quantity >= 0 AND change_quantity <> 0 AND before_quantity + change_quantity = after_quantity', name='balanced_quantities'),
        CheckConstraint("kind IN ('material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound','putaway','transfer','count_gain','count_loss','count_match','adjustment','reversal')", name='valid_kind'),
        CheckConstraint("(kind = 'reversal' AND reverses_transaction_id IS NOT NULL) "
                        "OR (kind <> 'reversal' AND reverses_transaction_id IS NULL)", name='valid_reversal'),
    )


class WarningRule(Identity, Timestamps, Base):
    # 保存临期天数阈值；预警状态由查询时的业务日期和当前正库存实时计算，不持久化到批次。
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
    # 业务审计与对应库存事务一起提交；用户名和描述快照用于账号改名后仍可追溯当时操作。
    __tablename__ = 'operation_logs'
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    username_snapshot: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(500))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
