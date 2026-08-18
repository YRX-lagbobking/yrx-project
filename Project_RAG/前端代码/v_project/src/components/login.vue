<template>
  <!-- 背景轮播容器（不影响表单交互） -->
  <div class="bg-carousel">
    <div class="bg-slide" :style="{ backgroundImage: `url(${bg1})` }"></div>
    <div class="bg-slide" :style="{ backgroundImage: `url(${bg2})` }"></div>
    <div class="bg-slide" :style="{ backgroundImage: `url(${bg3})` }"></div>
  </div>

  <!-- 登录卡片（位于上层，功能不变） -->
  <div class="login-container">
    <el-card class="login-card" shadow="hover">
      <h2 class="login-title">欢迎登录</h2>
      <!-- ===== 新增：登录模式切换标签 ===== -->
      <div class="mode-tabs">
        <span
          :class="{ active: loginMode === 'code' }"
          @click="switchMode('code')"
        >验证码登录</span>
        <span
          :class="{ active: loginMode === 'password' }"
          @click="switchMode('password')"
        >密码登录</span>
        <span
          :class="{ active: loginMode === 'register' }"
          @click="switchMode('register')"
        >注册</span>
      </div>
      <!-- ===== 模式切换标签结束 ===== -->
      <el-form label-width="80px" label-position="left" @submit.prevent>
        <el-form-item label="邮箱号">
          <el-input
            v-model="email"
            :disabled="loginMode === 'code' && !isCode"
            id="email"
            placeholder="请输入邮箱"
            clearable
          />
        </el-form-item>

        <!-- 验证码输入框：仅在验证码登录模式下显示 -->
        <el-form-item label="验证码" v-show="loginMode === 'code'">
          <el-input
            v-model="code"
            :disabled="isCode"
            placeholder="请输入验证码"
            clearable
          />
        </el-form-item>

        <!-- ===== 新增：密码输入框（密码登录和注册模式显示） ===== -->
        <el-form-item label="密码" v-show="loginMode === 'password' || loginMode === 'register'">
          <el-input
            v-model="password"
            type="password"
            placeholder="请输入密码"
            clearable
            show-password
          />
        </el-form-item>

        <!-- ===== 新增：用户名输入框（仅注册模式显示） ===== -->
        <el-form-item label="用户名" v-show="loginMode === 'register'">
          <el-input
            v-model="username"
            placeholder="请输入用户名"
            clearable
          />
        </el-form-item>

        <el-form-item>
          <!-- 验证码模式按钮：发送验证码 -->
          <el-button
            type="primary"
            @click="sendEmail"
            v-show="loginMode === 'code' && isCode"
            round
            class="login-btn"
          >


             发送验证码
          </el-button>
          <!-- 验证码模式按钮：验证验证码 -->
          <el-button
            type="success"
            @click="checkCode"
            v-show="loginMode === 'code' && !isCode"
            round
            class="login-btn"
          >
            ✅ 验证验证码
          </el-button>
          <!-- ===== 新增：密码登录按钮 ===== -->
          <el-button
            type="primary"
            @click="loginByPassword"
            v-show="loginMode === 'password'"
            round
            class="login-btn"
          >
            🔑 密码登录
          </el-button>
          <!-- ===== 新增：注册按钮 ===== -->
          <el-button
            type="success"
            @click="register"
            v-show="loginMode === 'register'"
            round
            class="login-btn"
          >
            📝 注册
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>

// ========== 以下是你原有的功能代码，所有注释均未删除 ==========
import { ref, getCurrentInstance } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import bg1 from "@/assets/images/bg2.jpg";
import bg2 from "@/assets/images/bg3.jpg";
import bg3 from "@/assets/images/bg7.jpg";
//创建路由跳转对象
let router = useRouter();
//定义输入框是否可以输入变量
let isCode = ref(true);
let email = ref("2779691651@qq.com");
let code = ref("");

// ===== 新增：登录模式、密码、用户名变量 =====
let loginMode = ref('code'); // 当前登录模式：'code'验证码登录 | 'password'密码登录 | 'register'注册
let password = ref('');       // 密码
let username = ref('');       // 用户名（注册用）

//获取当前实例对象
let proxy = getCurrentInstance().proxy;//只有通过这个实例对象才能访问得到main.js里面的$axios
//定义发送验证码的函数
function sendEmail() {
  ElMessage.primary("发送验证码中")
  let sendEmail = email.value;
  //通过封装的axios请求访问服务器定义的发送验证码接口
  proxy.$axios(
      { // {}中写的就是访问服务器接口的信息：比如请求地址
        // 【自动拼接前缀http://localhost:8000/】、请求方式【默认get】、请求参数
          url:"users/sendEmail",//请求地址
          method:"get",//请求方式
          params:{//请求参数
              email:sendEmail//参数的key（：的前面的）必须和服务器接口的形参一致
          },
      }
  ).then(res=>{
    let code = res.data.code;
    let msg = res.data.msg;
    let data = res.data.data;
    if(code===200){
      //修改isCode变量的值
      isCode.value = !isCode.value;
      sessionStorage.setItem("username",data);
      ElMessage.success(msg);
    }else{
      ElMessage.error(msg)
    }
  }).catch(err => {
    // 网络错误、CORS、500 等都会进这里
    console.error("发送验证码请求失败：", err);
    if (err.response) {
      // 服务器返回了响应，但状态码不是 2xx
      ElMessage.error("发送失败：" + (err.response.data?.msg || err.response.statusText));
    } else if (err.request) {
      // 请求发出去了但没收到响应（后端没跑/网络不通）
      ElMessage.error("发送失败：无法连接服务器，请确认后端是否启动");
    } else {
      // 请求都没发出去
      ElMessage.error("发送失败：" + err.message);
    }
  });
}
//定义验证码函数
function  checkCode(){
       // 延迟函数 setTimeout(function, time) --- 延迟time毫秒，执行function
      setTimeout(()=>{
        let checkCode ={// 把输入的邮箱号和验证码封装成一个js对象.因为
                       //后端是post请求，所以参数必须封装成一个json对象
        email:email.value,
        code:code.value
      }
      proxy.$axios(
          {
            url:"users/checkCode",
            method:"post",
            data: JSON.stringify(checkCode)
          }
      ).then(res=>{
        let code = res.data.code;
        if (code===200){
           let token = res.data.data.token;
           let username=res.data.data.username;
           let user_id=res.data.data.user_id
          console.log(username)
           sessionStorage.setItem("token", token); // 存起来
          sessionStorage.setItem("username", username); // username
          sessionStorage.setItem("user_id",user_id)
          ElMessage.success(res.data.msg)
          // alert("验证码正确")
           router.push("./chat");
        }else{
          ElMessage.error(res.data.msg)
          // alert(res.data.msg)
        }
      })
  },2000)

}
// ===== 新增：切换登录模式的函数 =====
function switchMode(mode) {
  loginMode.value = mode;
}

// ===== 新增：密码登录函数 =====
function loginByPassword() {
  setTimeout(()=>{
         ElMessage.primary("密码登录中，喵~");
         let loginData = {
      email: email.value,
      password: password.value};
      proxy.$axios({
        url: "users/loginByPwd",  // 后端接口地址（后续补全）
        method: "post",
        data:JSON.stringify(loginData)
      }).then(res => {
        let code = res.data.code;
        let msg = res.data.msg;
        let data = res.data.data;
        if (code === 200) {
          let token = data.token;       // 拿到 token
          let username=data.username
          sessionStorage.setItem("token", token);
          sessionStorage.setItem("username",username);
          sessionStorage.setItem("user_id",data.user_id)
          ElMessage.success(res.data.msg);
          router.push("./chat");
        } else {
          ElMessage.error(msg);
        }
      });
  },2000);

}

// ===== 新增：注册函数=====
function register() {
  ElMessage.primary("注册中，喵~");
  let registerData = {
    email: email.value,
    username: username.value,
    password: password.value
  };
  proxy.$axios({
    url: "users/register",  // 后端接口地址（后续补全）
    method: "post",
    data: JSON.stringify(registerData)
  }).then(res => {
    let code = res.data.code;
    let msg = res.data.msg;
    if (code === 200) {
      ElMessage.success(msg);
      // 注册成功后自动切换到密码登录模式
      loginMode.value = 'password';
    } else {
      ElMessage.error(msg);
    }
  });
}
// ========== 功能代码结束 ==========
</script>

<style scoped>
/* ===== 背景轮播 ===== */
.bg-carousel {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 0;
  overflow: hidden;
  pointer-events: none; /* 让鼠标事件穿透，不影响表单操作 */
}

.bg-slide {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-size: cover;
  background-position: center;
  opacity: 0;
  animation: slideAnimation 18s infinite;
}

/* 三张图片依次轮播，每张显示6秒，切换3秒 */
.bg-slide:nth-child(1) {
  animation-delay: 0s;
}
.bg-slide:nth-child(2) {
  animation-delay: 6s;
}
.bg-slide:nth-child(3) {
  animation-delay: 12s;
}

@keyframes slideAnimation {
  0% { opacity: 0; transform: scale(1.05); }
  8% { opacity: 1; transform: scale(1); }
  33% { opacity: 1; transform: scale(1); }
  41% { opacity: 0; transform: scale(1.05); }
  100% { opacity: 0; transform: scale(1.05); }
}

/* 流动光效（浮动光晕） */
.bg-carousel::after {
  content: '';
  position: absolute;
  top: -20%;
  left: -20%;
  width: 140%;
  height: 140%;
  background: radial-gradient(circle at 30% 40%, rgba(200, 240, 220, 0.15), transparent 60%),
              radial-gradient(circle at 70% 80%, rgba(180, 220, 240, 0.12), transparent 50%);
  animation: floatGlow 12s ease-in-out infinite alternate;
  pointer-events: none;
}

@keyframes floatGlow {
  0% { transform: translate(0, 0) rotate(0deg); }
  100% { transform: translate(3%, 2%) rotate(5deg); }
}

/* ===== 登录卡片（玻璃质感增强） ===== */
.login-container {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 20px;
}

.card-frame {
  width: 100%;
  max-width: 440px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.25) !important;  /* 更透的玻璃 */
  backdrop-filter: blur(16px) saturate(1.2);
  -webkit-backdrop-filter: blur(16px) saturate(1.2);
  border: 1px solid rgba(255, 255, 255, 0.4);
  box-shadow: 0 20px 60px rgba(0, 20, 20, 0.08);
  padding: 10px 20px 20px 20px;
  transition: all 0.3s ease;
}

.login-card {
  width: 100%;
  max-width: 440px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.25) !important;  /* 更透的玻璃 */
  backdrop-filter: blur(16px) saturate(1.2);
  -webkit-backdrop-filter: blur(16px) saturate(1.2);
  border: 1px solid rgba(255, 255, 255, 0.4);
  box-shadow: 0 20px 60px rgba(0, 20, 20, 0.08);
  padding: 10px 20px 20px 20px;
  transition: all 0.3s ease;
}

.login-title {
  text-align: center;
  color: #2d5a5a;
  font-weight: 500;
  font-size: 26px;
  letter-spacing: 2px;
  margin-top: 8px;
  margin-bottom: 24px;
  text-shadow: 0 2px 4px rgba(255,255,255,0.3);
}

/* ===== 新增：登录模式切换标签样式 ===== */
.mode-tabs {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-bottom: 24px;
}

.mode-tabs span {
  padding: 8px 20px;
  border-radius: 20px;
  font-size: 14px;
  color: #5d7a8c;
  cursor: pointer;
  transition: all 0.3s ease;
  background: rgba(255, 255, 255, 0.4);
  backdrop-filter: blur(4px);
  border: 1px solid rgba(255, 255, 255, 0.3);
  user-select: none;
}

.mode-tabs span:hover {
  background: rgba(255, 255, 255, 0.7);
  color: #3d6b6b;
}

.mode-tabs span.active {
  background: linear-gradient(135deg, #7ec8e3, #6ab7d4);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(126, 200, 227, 0.3);
}

:deep(.el-form-item__label) {
  color: #3d6b6b;
  font-weight: 500;
  font-size: 14px;
}

:deep(.el-input__wrapper) {
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.6) !important;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  border: 1px solid rgba(255, 255, 255, 0.3);
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
  transition: all 0.25s ease;
}

:deep(.el-input__wrapper:hover) {
  background: rgba(255, 255, 255, 0.8) !important;
  box-shadow: 0 0 0 2px rgba(129, 199, 132, 0.2);
}

:deep(.el-input__wrapper.is-focus) {
  background: rgba(255, 255, 255, 0.9) !important;
  box-shadow: 0 0 0 3px rgba(129, 199, 132, 0.3);
  border-color: #81c784;
}

:deep(.el-input.is-disabled .el-input__wrapper) {
  background: rgba(240, 244, 245, 0.5) !important;
  opacity: 0.6;
}

.login-btn {
  width: 100%;
  margin-top: 4px;
  border-radius: 30px;
  font-weight: 500;
  letter-spacing: 1px;
  padding: 12px 0;
  transition: all 0.25s ease;
  border: none;
  backdrop-filter: blur(4px);
}

.el-button--primary {
  background: linear-gradient(135deg, #7ec8e3, #6ab7d4) !important;
  color: white;
  box-shadow: 0 4px 12px rgba(126, 200, 227, 0.3);
}
.el-button--primary:hover {
  background: linear-gradient(135deg, #6ab7d4, #58a9c6) !important;
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(126, 200, 227, 0.4);
}

.el-button--success {
  background: linear-gradient(135deg, #a8d5a2, #94c78e) !important;
  color: white;
  box-shadow: 0 4px 12px rgba(168, 213, 162, 0.3);
}
.el-button--success:hover {
  background: linear-gradient(135deg, #94c78e, #80b97a) !important;
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(168, 213, 162, 0.4);
}

.el-button:active {
  transform: scale(0.96);
}
</style>