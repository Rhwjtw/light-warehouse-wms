import { createApp } from 'vue'
import {
  ElAlert, ElAside, ElAvatar, ElButton, ElCard, ElCol, ElContainer,
  ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElForm,
  ElFormItem, ElHeader, ElIcon, ElInput, ElInputNumber, ElLoading, ElMain,
  ElMenu, ElMenuItem, ElOption, ElPagination, ElRow, ElSelect, ElSkeleton,
  ElSkeletonItem, ElSubMenu, ElSwitch, ElTabPane, ElTable, ElTableColumn,
  ElTabs, ElTag, ElTooltip, provideGlobalConfig,
} from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/aside/style/css'
import 'element-plus/es/components/avatar/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/container/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/header/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/main/style/css'
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/menu-item/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/skeleton/style/css'
import 'element-plus/es/components/skeleton-item/style/css'
import 'element-plus/es/components/sub-menu/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'
import './style.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(router)

// 仅注册模板实际使用的组件和指令，避免全量安装 Element Plus。
for (const component of [
  ElAlert, ElAside, ElAvatar, ElButton, ElCard, ElCol, ElContainer,
  ElDatePicker, ElDescriptions, ElDescriptionsItem, ElDialog, ElForm,
  ElFormItem, ElHeader, ElIcon, ElInput, ElInputNumber, ElLoading, ElMain,
  ElMenu, ElMenuItem, ElOption, ElPagination, ElRow, ElSelect, ElSkeleton,
  ElSkeletonItem, ElSubMenu, ElSwitch, ElTabPane, ElTable, ElTableColumn,
  ElTabs, ElTag, ElTooltip,
]) app.use(component)

provideGlobalConfig({ locale: zhCn }, app, true)
app.mount('#app')
