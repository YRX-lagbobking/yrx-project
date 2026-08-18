import { createApp } from 'vue'
import './style.css'
import App from './App.vue'


//创建对象
const app = createApp(App)

//路由对象的注册
import router from "./router"
app.use(router)

app.mount('#app')
//axios全局配置
import axios from 'axios'//导入axios包
axios.defaults.baseURL = 'http://localhost:8000/'//服务器访问公共部分
axios.defaults.headers.post['Content-Type'] = 'application/json'//post请求发送json数据给服务器
// axios.defaults.headers.put['Content-Type'] = 'application/json'//put请求发送json数据给服务器
app.config.globalProperties.$axios = axios//挂载axios,使用对象。$axios替代原生的axios
//----------------------
  // 请求拦截器：自动带 token
axios.interceptors.request.use(config => {
    const token = sessionStorage.getItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
//----------------------
//注册element-plus
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
app.use(ElementPlus)

//通过导航钩子实现未登录拦截
router.beforeEach((to,from,next)=>{
    //to:去哪里
    //from:从哪里来
    //next:是否执行处理
    if (to.meta.isLogin) {
        let username = sessionStorage.getItem("username");
        if (username) {
            //放行
            next();
        } else {
            //拦截---让它回到登陆页面
            next('/');
        }
    }else{
        //直接放行
        next();
    }
})