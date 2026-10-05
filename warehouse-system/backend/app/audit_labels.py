"""Human-readable summaries; deliberately exclude raw request bodies and secrets."""
MODULES = {'users': '用户账户', 'raw_materials': '原材料档案', 'finished_products': '成品档案',
           'suppliers': '供应商', 'locations': '库位', 'bom_headers': 'BOM配方',
           'production_orders': '生产单', 'stock_documents': '库存单据', 'warning_rules': '预警设置'}
ACTIONS = {'login': '登录', 'login_failed': '登录失败', 'logout': '退出登录', 'change_password': '修改密码',
           'create': '新增档案', 'update': '修改档案', 'archive': '删除档案',
           'create_user': '新增用户', 'update_user': '修改用户', 'archive_user': '删除用户', 'reset_password': '重置密码',
           'create_bom': '创建BOM', 'update_bom': '修改BOM', 'update_bom_status': '修改BOM状态', 'delete_bom': '删除BOM',
           'material_receipt': '原材料入库', 'material_outbound': '原材料出库',
           'finished_receipt': '成品入库', 'finished_outbound': '成品出库',
           'create_production_order': '创建生产单', 'update_production_order': '修改生产单',
           'cancel_production_order': '取消生产单', 'complete_production_order': '完成生产',
           'production_issue': '生产领料', 'production_supplement': '生产补料', 'production_return': '生产退料',
           'register_actual_quantity': '登记实际产量', 'update_settings': '修改预警设置',
           'reverse_stock_document': '冲销库存单据'}
