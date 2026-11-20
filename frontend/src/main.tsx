import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import ConfigProvider from 'antd/es/config-provider'
import { App } from './app'
import './style.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode><ConfigProvider theme={{token:{
    colorPrimary:'#315c43', colorText:'#20342b', colorBgElevated:'#fcfdf8',
    borderRadius:10, fontFamily:'"Aptos","Microsoft YaHei","PingFang SC",sans-serif',
  }}}><App/></ConfigProvider></StrictMode>
)
