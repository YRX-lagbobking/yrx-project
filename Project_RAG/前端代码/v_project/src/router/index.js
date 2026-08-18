//引入路由配置文件
import {createRouter,createWebHistory}from "vue-router";

//定义路由配置对象 ---数组
const routes =[
    //前面两个是测试页面，就不修改了
    {
        path:"",
        meta:{
          isLogin:false,//isLogin自定义属性，false表示未登录直接访问
        },
        component:()=>import("../components/login.vue")
    },
    {
        path:"/chat",
        meta:{
            isLogin: true,//isLogin自定义属性，true表示登录直接访问
        },
        component:()=>import("../components/chat.vue")
    },
]
//设置路由模式为history模式---默认hash模式，访问路径中有一个#号
const router = createRouter({
    history:createWebHistory(),
    routes: routes
})
//导出路由实例
export default router