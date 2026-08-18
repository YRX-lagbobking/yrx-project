<template>
  <div class="chat-container" :style="{ backgroundImage: `url(${currentBg})` }">
    <!-- ===== 左侧侧边栏 ===== -->
    <aside class="sidebar" :style="{ backgroundImage: `url(${sidebarBgImages[sidebarBgIndex]})` }">
      <div class="sidebar-header">
        <div class="sidebar-logo">
          <span class="logo-icon">🌿</span>
          <span class="sidebar-title">AI 助手</span>
        </div>
        <el-button class="new-chat-btn" type="primary" size="small" round @click="newChat">
          <span>+ 新对话</span>
        </el-button>
      </div>

      <div class="sidebar-search">
        <el-input
            v-model="searchKeyword"
            placeholder="搜索历史记录..."
            size="small"
            clearable
            prefix-icon="Search"
            @input="handleSearchInput"
            @clear="clearSearch"
        />
      </div>
      <div class="history-list">
        <!-- 1️⃣ 有搜索结果：显示搜索结果 -->
        <template v-if="searchResults.length > 0">
          <div
              v-for="(item, index) in searchResults"
              :key="item.historyId"
              class="history-item"
              :class="{ active: item.active }"
              @click="conversationLog(item.historyId)"
          >
            <span class="history-icon">📄</span>
            <div class="history-content">
              <span class="history-title">{{ item.title }}</span>
              <span class="history-time">{{ item.time }}</span>
            </div>
            <div class="history-actions">
              <el-popconfirm
                  title="确认删除该对话吗？"
                  @confirm="deleteHistory(item.historyId)"
              >
                <template #reference>
                  <el-button
                      type="danger"
                      size="small"
                      :icon="Delete"
                      circle
                      plain
                  />
                </template>
              </el-popconfirm>
            </div>
          </div>
        </template>

        <!-- 2️⃣ 正在搜索但无结果：显示“未找到” -->
        <div v-else-if="searchKeyword.trim() && searchResults.length === 0" class="empty-search">
          (˘▽˘)っ🌀喵！ 没有找到相关对话
        </div>

        <!-- 3️⃣ 默认（未搜索或搜索框为空）：显示全部历史 -->
        <template v-else>
          <div
              v-for="(item, index) in historyList"
              :key="item.historyId"
              class="history-item"
              :class="{ active: item.active }"
              @click="conversationLog(item.historyId)"
          >
            <span class="history-icon">📄</span>
            <div class="history-content">
              <span class="history-title">{{ item.title }}</span>
              <span class="history-time">{{ item.time }}</span>
            </div>
            <div class="history-actions">
              <el-popconfirm
                  title="确认删除该对话吗？"
                  @confirm="deleteHistory(item.historyId)"
              >
                <template #reference>
                  <el-button
                      type="danger"
                      size="small"
                      :icon="Delete"
                      circle
                      plain
                  />
                </template>
              </el-popconfirm>
            </div>
          </div>
        </template>
      </div>

      <div class="sidebar-footer">
        <!-- 点击头像弹出用户菜单：查看用户信息 / 修改个人信息 / 退出登录 -->
        <el-dropdown trigger="click" placement="top-start" popper-class="user-menu-popper" @command="handleUserMenuCommand">
          <div class="user-info">
            <el-avatar :size="32" class="user-avatar">{{ username.charAt(0).toUpperCase() }}</el-avatar>
            <span class="user-name">{{ username }}</span>
            <el-icon class="user-caret"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="view">查看用户信息</el-dropdown-item>
              <el-dropdown-item command="edit">修改个人信息</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <!-- ===== 右侧主聊天区域 ===== -->
    <main class="main-area">
      <!-- 顶部标题栏 -->
      <header class="chat-header">
        <div class="header-left">
          <span class="header-icon">💬</span>
          <span class="header-title">AI 智能对话</span>
        </div>
        <div class="header-right">
          <el-tooltip content="清空对话" placement="bottom">
            <el-button class="header-btn" circle size="small" @click="clearMessages">
              <span>🗑️</span>
            </el-button>
          </el-tooltip>
        </div>
      </header>

      <!-- 消息显示区域 -->
      <div class="message-area" ref="messageAreaRef">
        <!-- 空状态（仿老师设计） -->
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">🌱</div>
          <h2 class="empty-title">你好，{{ username }}</h2>
          <p class="empty-desc">有什么我可以帮助你的吗？</p>
          <div class="quick-prompts">
            <div
                v-for="(prompt, idx) in quickPrompts"
                :key="idx"
                class="prompt-chip"
                @click="question = prompt.text"
            >
              <span>{{ prompt.text }}</span>
            </div>
          </div>
        </div>

        <!-- 消息列表 -->
        <div
            v-for="(item, index) in messages"
            :key="index"
            class="message-item"
            :class="item.role"
        >
          <div class="message-avatar">
            <el-avatar :size="36" v-if="item.role === 'user'">
              {{ username.charAt(0).toUpperCase() }}
            </el-avatar>
            <el-avatar :size="36" v-else class="ai-avatar">AI</el-avatar>
          </div>
          <div class="message-body">
            <div class="message-role-name">
              {{ item.role === 'user' ? username : 'AI 助手' }}
            </div>
            <div class="message-bubble">
              <!--              <div class="message-content">{{ item.content }}</div>-->
              <div class="message-content" v-html="renderMarkdown(item.content)"></div>
            </div>
          </div>
        </div>


      </div>

      <!-- 底部输入区域 -->
      <footer class="input-area">
        <div class="input-wrapper">
          <el-input
              v-model="question"
              placeholder="输入你的问题，喵~按 Enter 发送..."
              class="chat-input"
              size="large"
              :disabled="isloading"
              @keyup.enter="chat"
              clearable
          />
          <el-button
              type="primary"
              @click="chat"
              :disabled="isloading"
              class="send-btn"
              round
          >
            {{ isloading ? '思考中...' : '发送' }}
          </el-button>
        </div>
        <p class="input-hint">AI 回答仅供参考，请核实重要信息</p>
      </footer>
    </main>

    <!-- ===== 修改个人信息弹窗 ===== -->
    <el-dialog
        v-model="editDialogVisible"
        title="修改个人信息"
        width="420px"
        :close-on-click-modal="false"
        class="edit-info-dialog"
    >
      <!-- 选择要修改的内容：用户名 / 密码 -->
      <div class="edit-type-tabs">
        <el-radio-group v-model="editType">
          <el-radio-button value="username">修改用户名</el-radio-button>
          <el-radio-button value="password">修改密码</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 修改用户名：显示曾用名，输入新用户名 -->
      <el-form v-if="editType === 'username'" label-width="70px" @submit.prevent>
        <el-form-item label="曾用名">
          <el-input :model-value="username" disabled />
        </el-form-item>
        <el-form-item label="新用户名">
          <el-input v-model="newUsername" placeholder="请输入新的用户名" clearable />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" round class="edit-info-btn" @click="submitUsername">确认修改</el-button>
        </el-form-item>
      </el-form>

      <!-- 修改密码：参考登录页密码框写法，并要求二次确认 -->
      <el-form v-else label-width="70px" @submit.prevent>
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" placeholder="请输入新密码" show-password clearable />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="confirmPassword" type="password" placeholder="请再次输入新密码" show-password clearable />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" round class="edit-info-btn" @click="submitPassword">确认修改</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- ===== 查看用户信息弹窗（半透明毛玻璃，文字深色保证清晰） ===== -->
    <el-dialog
        v-model="userListVisible"
        title="用户列表"
        width="640px"
        :close-on-click-modal="false"
        class="user-list-dialog"
    >
      <!-- 每一行代表一个用户，四列：用户ID / 用户名 / 邮箱 / 角色 -->
      <!-- prop 字段已和后端返回一致：user_id | username | email | role -->
      <el-table :data="userList" height="360px">
        <el-table-column prop="user_id" label="用户ID" width="90" />
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="email" label="邮箱" min-width="180" />
        <el-table-column prop="role" label="角色" width="100" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
// ========== 以下是你原有的功能代码 + 注释，一行未改 ==========
import {ref, getCurrentInstance, onMounted} from "vue";
import {ElMessage} from "element-plus";
import {Delete} from '@element-plus/icons-vue'  // 新增
import { fetchEventSource } from '@microsoft/fetch-event-source'//使用fetch
import bg4 from "@/assets/images/bg4.jpg";
import bg5 from "@/assets/images/bg5.jpg";
import bg6 from "@/assets/images/bg6.jpg";
// 侧边栏背景轮播图片
import sidebarBg1 from "@/assets/images/sidebar-bg.jpg";
import sidebarBg2 from "@/assets/images/chat-bg.jpg";
import sidebarBg3 from "@/assets/images/5.jpg";
import sidebarBg4 from "@/assets/images/6.jpg";
//创建代理对象
let proxy = getCurrentInstance().proxy;
//定义一个接收用户名称的变量
let username = ref("");
//定义用户问题
let question = ref("");
//定义一个接收用户问题的变量
let messages = ref([]);
//定义聊天按钮是否可以点击
let isloading = ref(false);
let user_id = ref("");

// 背景图列表（排除 login 使用的 bg2、bg3、bg7）
const bgImages = [bg4, bg5, bg6];
const currentBg = ref("");
// 侧边栏背景轮播
const sidebarBgImages = [sidebarBg1, sidebarBg2, sidebarBg3, sidebarBg4];
const sidebarBgIndex = ref(0);
// ===== 新增：搜索结果列表（用于后端搜索） =====
const searchResults = ref([]);
const isSearching = ref(false); // 可选：显示加载状态

//---------------------------------
//挂载函数
onMounted(() => {
  //加载页面后自动执行的代码
  username.value = sessionStorage.getItem("username");
  user_id.value = sessionStorage.getItem("user_id");
  //获取历史记录菜单栏
  query_history_menu();
  // 随机选择背景图
  const randomIndex = Math.floor(Math.random() * bgImages.length);
  currentBg.value = bgImages[randomIndex];
  // 侧边栏背景图轮播：每5秒切换一张
  setInterval(() => {
    sidebarBgIndex.value = (sidebarBgIndex.value + 1) % sidebarBgImages.length;
  }, 10000);
})

//========== chat函数 ==========
//chat函数
let chatController = null // 取消请求用的“挂断键”（替代 es.close() 的作用）

function chat() {
  isloading.value = true //此时就不能再继续输入问题
  console.log("正在执行chat函数1")
  let Question = question.value.trim() //去掉首位的空格和尾部的空格
  //清空输入框
  question.value = "";
  //判断是否输入了数据
  if (Question === "") {
    ElMessage.error("不要输入空白哟喵~");
    isloading.value = false
    return;
  }
  ;
  //在消息列表中添加对话
  messages.value.push({role: 'user', content: Question});
  messages.value.push({role: 'assistant', content: '喵！等俺一下喵~(ฅ^•ﻌ•^ฅ)~...'});

  //如果上一条消息的流还没结束，先挂断它，防止两个流写进同一个气泡
  if (chatController) {
    chatController.abort()
  }
  chatController = new AbortController()

  //定义一个变量来接收结果
  let s = "";

  // ✅  改为 fetchEventSource 发 POST，token 放进请求头
  fetchEventSource('http://localhost:8000/chat/chat', {
    method: 'POST', // ✅  对应后端 @chat_router.post("/chat")
    headers: {
      'Authorization': 'Bearer ' + sessionStorage.getItem("token"),
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      question: Question,
      history_id: currentChatId.value // ✅  字段名必须 history_id，和后端 ChatClass 一致
    }),
    signal: chatController.signal, // 绑定挂断键
    //监听服务器响应的流式输出（msg.data 和原来的 e.data 是一回事）
    onmessage(msg) {
      let data = JSON.parse(msg.data).content
      //由data内容判断是否要关闭连接
      if (data === "done") {
        saveChat(Question, s)//调用保存对话记录函数，
        chatController.abort() // 主动挂断（相当于原来的 es.close()）
        isloading.value = false
        return;
      }
      s += data;
      messages.value[messages.value.length - 1].content = s
    },
    onerror(err) {
      console.log("SSE发生错误：", err)
      ElMessage.error("服务器错误！");
      chatController.abort();
      isloading.value = false
      throw err // ⚠️ 必须 throw，否则这个库会自动重连，导致重复输出
    },
    onclose() {
      // 兜底恢复按钮状态
      isloading.value = false
    }
  }).catch(() => {
    // 吃掉 Promise 的 rejection，避免控制台刷 Uncaught (in promise)
  })
}

//========== 历史记录函数 ==========
function query_history_menu() {
  proxy.$axios({
    url: "chat/queryHistoryMenu",
    method: "get",
    params: {
      user_id: user_id.value
    }

  }).then(res => {
    historyList.value = res.data.data;
  });
};

//========== 查询某一条详细对话记录 ==========
function conversationLog(historyId) {
  currentChatId.value = historyId;//将historyId保存起来到当前页面的currentChatId变量中
  //：当你在左侧点击一条历史记录时，currentChatId 被更新为该记录的 historyId。这样，你接下来发送的消息，就会被后端追加到这个已有的对话窗口中，而不是新建一个。
  proxy.$axios({
    url: "chat/conversationLog",
    method: "get",
    params: {
      history_id: historyId
    }
  }).then(res => {
    messages.value = res.data.data;
  });
}

//========== 存储对话记录 ==========
function saveChat(question, answer) {

  console.log("正在执行saveChat函数")
  proxy.$axios({
    url: "chat/saveChat",
    method: "post",
    data: JSON.stringify({
      user_id: user_id.value,
      parent_id: currentChatId.value,
      answer: answer,
      question: question
    })
  }).then(res => {
    //let historyId = res.data.code;
    //只有当currentChatId=0时，才需要把新增的数据的historyId赋值给它【表示一个新对话的开始】
    //如果currentChatId不等于0，说明当前点击的是一个已有的对话窗口，那么就不需要再赋值了
    if (currentChatId.value === 0) {
      console.log(res.data.data)
      currentChatId.value = res.data.data;
    }
    //重新加载菜单栏
    query_history_menu();
  });
}

//========== 新对话 ==========
function newChat() {
  currentChatId.value = 0;
  messages.value = [];
}

//========== 删除对话记录 ==========
function deleteHistory(historyId) {
  proxy.$axios({
    url: "chat/deleteHistory",
    method: "post",
    data: JSON.stringify({
      history_id: historyId,
      user_id: user_id.value
    })
  }).then(res => {
    //重新加载菜单栏
    let code = res.data.code;
    if (code === 200) {
      ElMessage.success('删除成功啦喵');
      query_history_menu();
    } else {
      ElMessage.error('删除失败了喵呜呜呜');
    }
  }).catch(err => {
    if (err.response.status === 403) {
      ElMessage.error("你没有删除权限喵！，请联系管理员");
    } else {
      ElMessage.error("请求失败呜呜");
    }
  });
}

// ===== 新增：调用后端搜索接口 =====
function searchHistory() {
  const keyword = searchKeyword.value.trim();
  // 如果关键词为空，清空搜索结果并恢复显示历史列表
  if (!keyword) {
    searchResults.value = [];
    return;
  }
  isSearching.value = true;
  proxy.$axios({
    url: "chat/searchMessages", // 后端接口地址
    method: "get",
    params: {
      user_id: user_id.value,
      keyword: keyword
    }
  }).then(res => {
    if (res.data.code === 200) {
      searchResults.value = res.data.data; // 后端返回匹配的历史记录列表
    } else {
      ElMessage.warning(res.data.msg || "搜索失败");
    }
  }).catch(err => {
    console.error("搜索出错：", err);
    ElMessage.error("搜索请求失败");
  }).finally(() => {
    isSearching.value = false;
  });
}

// ===== 新增：防抖处理（避免频繁请求） =====
let searchTimer = null;

function handleSearchInput() {
  // 清除之前的定时器
  if (searchTimer) {
    clearTimeout(searchTimer);
    searchTimer = null;
  }
  // 如果输入框被清空，立即恢复菜单
  if (!searchKeyword.value.trim()) {
    searchResults.value = [];
    return;
  }
  // 延迟 500ms 执行搜索（等用户停止打字）
  searchTimer = setTimeout(() => {
    searchHistory();
  }, 500);
}

// 点击输入框的“清空”按钮时调用
function clearSearch() {
  searchResults.value = [];
  // 注意：v-model 已经自动清空了 searchKeyword，不需要手动置空
}

// ========== 功能代码结束 ==========

// ===== 新增辅助数据（只用于样式展示，不影响原有功能） =====
//定义一个接收历史记录的变量
const currentChatId = ref(0);//存储当前点击的是哪一个对话的窗口的historyId
const searchKeyword = ref("");
const historyList = ref([]);
const quickPrompts = ref([
  {text: "05年1月1日有啥新闻？"},
  {text: "告诉我有关雪的新闻"},
  {text: "社会新闻"},
  {text: "人民日报26年7月5日刊登了什么"},
  {text: "13年9月24日是我妹妹的生日"},
]);

// 清空对话（只清空消息列表，不影响后端）
function clearMessages() {
  messages.value = [];
  ElMessage.success("已清空对话");
}

// =======markdown格式添加===============
import {marked} from 'marked'
import DOMPurify from 'dompurify'

// 将 Markdown 转为安全的 HTML
function renderMarkdown(text) {
  if (!text) return ''
  // 解析 Markdown
  const rawHtml = marked.parse(text)
  // 净化防 XSS
  return DOMPurify.sanitize(rawHtml)
}

// ========== 用户菜单（点击头像）功能 ==========
import {useRouter} from "vue-router";//退出登录后跳转页面用
import {ArrowDown} from '@element-plus/icons-vue'//头像旁边的小箭头图标

const router = useRouter();

//头像菜单点击分发：el-dropdown 会把被点击选项的 command 传进来
function handleUserMenuCommand(command) {
  if (command === 'view') {
    showUserInfo();
  } else if (command === 'edit') {
    editUserInfo();
  } else if (command === 'logout') {
    handleLogout();
  }
}

//========== 查看用户信息 ==========
//用户列表弹窗相关变量
const userListVisible = ref(false); // 控制弹窗显示/隐藏
const userList = ref([]);           // 后端返回的用户列表，每一行是一个用户

function showUserInfo() {
  // 1、先打开弹窗，并把列表清空（每次重新拉取）
  userListVisible.value = true;
  userList.value = [];
  // 2、调用后端接口获取用户列表（⚠️ url 改成你后端的实际接口路径）
  proxy.$axios({
    url: "users/getUserList",
    method: "get"
  }).then(res => {
    let code = res.data.code;
    // 3、code === 200 才取 data 里的列表；
    // 不是 200 就不赋值，保持空列表，弹窗显示空表格即可
    if (code === 200) {
      userList.value = res.data.data;
    }
  }).catch(err => {
    if (err.response.status === 403) {
      ElMessage.error("你没有权限喵！，请联系管理员");
    } else {
      ElMessage.error("请求失败呜呜");
    }
  });
}

//========== 修改个人信息 ==========
//弹窗相关变量
const editDialogVisible = ref(false); // 控制弹窗显示/隐藏
const editType = ref("username");     // 当前修改类型：username 修改用户名 | password 修改密码
const newUsername = ref("");          // 新用户名
const newPassword = ref("");          // 新密码
const confirmPassword = ref("");      // 确认新密码

function editUserInfo() {
  // 每次打开弹窗前，把表单内容重置一遍
  newUsername.value = "";
  newPassword.value = "";
  confirmPassword.value = "";
  editType.value = "username";
  editDialogVisible.value = true;
}

//========== 提交修改用户名 ==========
function submitUsername() {
  // 1、简单校验：新用户名不能为空
  if (!newUsername.value.trim()) {
    ElMessage.error("新用户名不能为空喵~");
    return;
  }
  // 2、调用后端接口（⚠️ url 改成你后端的实际接口路径）
  proxy.$axios({
    url: "users/updateUsername",
    method: "post",
    data: JSON.stringify({
      old_username: username.value,      // 曾用名
      new_username: newUsername.value.trim()
    })
  }).then(res => {
    let code = res.data.code;
    if (code === 200) {
      // 3、修改成功后，同步更新页面上的用户名和 sessionStorage
      username.value = newUsername.value.trim();
      sessionStorage.setItem("username", username.value);
      ElMessage.success(res.data.msg);
      editDialogVisible.value = false;
    } else {
      ElMessage.error(res.data.msg);
    }
  }).catch(err => {
    console.error("修改用户名失败：", err);
    ElMessage.error("请求失败，请确认后端已启动");
  });
}

//========== 提交修改密码 ==========
function submitPassword() {
  // 新密码不能为空
  if (!newPassword.value) {
    ElMessage.error("新密码不能为空喵~");
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    ElMessage.error("两次输入的密码不一致，请重新输入");
    return;
  }
  //
  proxy.$axios({
    url: "users/updatePassword",
    method: "post",
    data: JSON.stringify({
      username: username.value,
      password: newPassword.value
    })
  }).then(res => {
    let code = res.data.code;
    if (code === 200) {
      ElMessage.success(res.data.msg || "密码修改成功");
      editDialogVisible.value = false;
    } else {
      ElMessage.error(res.data.msg || "修改失败");
    }
  }).catch(err => {
    console.error("修改密码失败：", err);
    ElMessage.error("请求失败，请确认后端已启动");
  });
}

//========== 退出登录 ==========
async function handleLogout() {
  try {
    // 1、调用后端登出接口（项目统一用 proxy.$axios，token 存在 sessionStorage）
    const token = sessionStorage.getItem("token");
    proxy.$axios.post("users/logOut", JSON.stringify({token}));
    // 2、清除本地登录状态
    sessionStorage.removeItem("token");
    sessionStorage.removeItem("username");
    sessionStorage.removeItem("user_id")
    // 3、提示并跳回登录页（登录页路由是 "/"）
    ElMessage.success("已退出登录");
    router.push("/");
  } catch (error) {
    console.error("登出失败", error);
    ElMessage.error("登出失败");
  }
}
// ========== 用户菜单功能结束 ==========
</script>

<style scoped>
/* ===== 全局背景 ===== */
.chat-container {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: center/cover no-repeat fixed;
  font-family: 'Segoe UI', 'PingFang SC', Roboto, sans-serif;
}

/* ===== 侧边栏（加背景图 + 白色遮罩） ===== */
.sidebar {
  width: 280px;
  min-width: 280px;
  height: 100vh;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  position: relative;
  display: flex;
  flex-direction: column;
  user-select: none;
  border-right: 1px solid rgba(255, 255, 255, 0.12); /* 白色边框 */
}

/* 半透明白色遮罩层，保证文字可读 */
.sidebar::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  z-index: 0;
}

.sidebar > * {
  position: relative;
  z-index: 1;
}

.sidebar-header {
  padding: 20px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.15);

}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #1e3a3a;
}

.logo-icon {
  font-size: 24px;
}

.sidebar-title {
  font-size: 18px;
  font-weight: 700;
  color: #b8d5ce;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 2px rgba(255, 255, 255, 0.5);
}

.new-chat-btn {
  width: 100%;
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  border: none !important;
  font-weight: 500;
  color: #fff !important;
  transition: all 0.3s ease;
  box-shadow: 0 2px 8px rgba(78, 168, 201, 0.2);
}

.new-chat-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(78, 168, 201, 0.4) !important;
}

.sidebar-search {
  padding: 12px 16px;
}

.sidebar-search :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 20px;
  box-shadow: none;
  backdrop-filter: blur(2px);
}

.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
}

.history-list::-webkit-scrollbar {
  width: 4px;
}

.history-list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.5);
  border-radius: 4px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 2px;
  border: 1px solid transparent;
}

.history-item:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.3);
}

.history-item.active {
  background: rgba(255, 255, 255, 0.1);
  border-color: #6abf9e;
}

.history-icon {
  font-size: 16px;
}

.history-content {
  flex: 1;
  min-width: 0;
}

.history-title {
  font-size: 13px;
  font-weight: 500;
  color: #94c78e;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-time {
  font-size: 11px;
  color: #ffffff;
}

.history-more {
  color: #4a6f6f;
  font-weight: bold;
}

/* 左侧搜索框输入文字颜色 */
.sidebar-search :deep(.el-input__inner) {
  color: #e0f0e8; /* 浅蓝绿色，匹配你的小清新主题，你可以自定义 */
}

/* 左侧搜索框占位文字颜色 */
.sidebar-search :deep(.el-input__inner::placeholder) {
  color: rgba(255, 255, 255, 0.5);
}

/* 左侧搜索框聚焦时：底变白、字变深，和聊天输入框一致 */
.sidebar-search :deep(.el-input__wrapper.is-focus) {
  background: rgba(255, 255, 255, 0.9);
  border-color: #6abf9e;
  box-shadow: 0 0 0 3px rgba(106, 191, 158, 0.15);
}

.sidebar-search :deep(.el-input__wrapper.is-focus) .el-input__inner {
  color: #1e3a3a;
}

.sidebar-search :deep(.el-input__wrapper.is-focus) .el-input__inner::placeholder {
  color: rgba(0, 0, 0, 0.4);
}

.sidebar-footer {
  padding: 14px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.5);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.2s;
}

.user-info:hover {
  background: rgba(255, 255, 255, 0.12);
}

.user-caret {
  margin-left: auto;
  color: #6abf9e;
  font-size: 14px;
}

.user-avatar {
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  color: #fff !important;
  font-weight: 600;
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: #6abf9e;
  text-shadow: 0 1px 2px rgba(255, 255, 255, 0.3);
}

/* ===== 主聊天区域（玻璃效果，大幅降低透明度） ===== */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  border-left: 1px solid rgba(255, 255, 255, 0.3);
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  background: rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(4px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.3);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-icon {
  font-size: 20px;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #1e3a3a;
}

.header-btn {
  border: 1px solid rgba(255, 255, 255, 0.4) !important;
  color: #4a7a72 !important;
  background: rgba(255, 255, 255, 0.2);
  transition: all 0.2s;
}

.header-btn:hover {
  border-color: #6abf9e !important;
  background: rgba(106, 191, 158, 0.15) !important;
}

.message-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px;
  scroll-behavior: smooth;
}

.message-area::-webkit-scrollbar {
  width: 6px;
}

.message-area::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.4);
  border-radius: 3px;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: #1e3a3a;
}

.empty-icon {
  font-size: 64px;
}

.empty-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  text-shadow: 0 1px 2px rgba(255, 255, 255, 0.3);
}

.empty-desc {
  font-size: 15px;
  color: #4a6f6f;
}

.quick-prompts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  max-width: 500px;
  margin-top: 12px;
}

.prompt-chip {
  padding: 8px 16px;
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.6);
  background: rgba(255, 255, 255, 0.35);
  color: #2d5a5a;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  backdrop-filter: blur(2px);
}

.prompt-chip:hover {
  border-color: #6abf9e;
  background: rgba(106, 191, 158, 0.15);
  transform: translateY(-1px);
}

/* ===== 消息气泡（高不透明度，保证清晰） ===== */
.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  animation: fadeIn 0.3s ease;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-item.user .message-body {
  align-items: flex-end;
}

.message-item.user .message-role-name {
  text-align: right;
}

.message-avatar {
  flex-shrink: 0;
  margin-top: 4px;
}

.ai-avatar {
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  color: #fff !important;
  font-weight: 600;
}

.message-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 70%;
}

.message-role-name {
  font-size: 12px;
  font-weight: 600;
  color: #4a6f6f;
  padding: 0 4px;
}

.message-bubble {
  padding: 12px 18px;
  border-radius: 20px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  /* 背景不透明或高不透明度 */
  background: rgba(255, 255, 255, 0.95) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.message-item.user .message-bubble {
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  color: #fff;
  border-bottom-right-radius: 6px;
  box-shadow: 0 4px 12px rgba(78, 168, 201, 0.25);
}

.message-item.assistant .message-bubble {
  background: rgba(255, 255, 255, 0.9) !important;
  color: #a8d5a2;
  border-bottom-left-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.5);
}

/* 打字动画（如果使用） */
.typing-bubble {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 16px 18px !important;
}

.typing-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7a9e96;
  animation: bounce 1.4s infinite ease-in-out;
}

.typing-dot:nth-child(1) {
  animation-delay: 0s;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 60%, 100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-6px);
    opacity: 1;
  }
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ===== 底部输入区域（玻璃质感，降低透明度） ===== */
.input-area {
  padding: 16px 24px 20px;
  background: rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(8px);
  border-top: 1px solid rgba(255, 255, 255, 0.3);
}

.input-wrapper {
  display: flex;
  gap: 12px;
  max-width: 900px;
  margin: 0 auto;
  align-items: center;
}

.chat-input {
  flex: 1;
}

.chat-input :deep(.el-input__wrapper) {
  border-radius: 30px;
  padding: 6px 18px;
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.4);
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.02);
  transition: all 0.3s;
}

.chat-input :deep(.el-input__wrapper.is-focus) {
  border-color: #6abf9e;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 0 0 3px rgba(106, 191, 158, 0.15);
}

.chat-input :deep(.el-input__inner) {
  font-size: 14px;
  color: #1e3a3a;
}

.send-btn {
  flex-shrink: 0;
  padding: 12px 24px;
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  border: none !important;
  font-weight: 500;
  color: #fff !important;
  transition: all 0.3s ease;
}

.send-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(78, 168, 201, 0.35) !important;
}

.send-btn:active {
  transform: scale(0.96);
}

.send-btn.is-disabled {
  background: #b8d5ce !important;
  box-shadow: none !important;
  transform: none !important;
}

.input-hint {
  text-align: center;
  font-size: 11px;
  color: #4a6f6f;
  margin-top: 8px;
}

/* 新增：历史记录操作按钮区域 */
.history-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  margin-left: 4px;
}

.history-actions .el-button {
  padding: 4px;
  border: none;
  background: transparent;
  color: #ccc;
  font-size: 14px;
  transition: color 0.2s;
}

.history-actions .el-button:hover {
  color: #94c78e;
}

/* 搜索无结果提示样式 */
.empty-search {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: rgba(255, 255, 255, 0.6);
  font-size: 14px;
  text-align: center;
}

/* markdown格式增加*/
.message-content h1,
.message-content h2,
.message-content h3 {
  margin: 0.5em 0;
}

.message-content ul,
.message-content ol {
  padding-left: 1.5em;
  margin: 0.5em 0;
}

.message-content code {
  background: rgba(0, 0, 0, 0.06);
  padding: 0.2em 0.4em;
  border-radius: 3px;
  font-family: monospace;
}

.message-content pre {
  background: #f6f8fa;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}

.message-content blockquote {
  border-left: 4px solid #6abf9e;
  padding-left: 12px;
  margin: 0.5em 0;
  color: #4a6f6f;
}
</style>

<style>
/* ===== 头像下拉菜单（菜单挂在 body 上，scoped 样式管不到，写在这里） ===== */
.user-menu-popper .el-dropdown-menu__item {
  font-size: 13px;
}

.user-menu-popper .el-dropdown-menu__item:not(.is-disabled):hover {
  background: rgba(106, 191, 158, 0.12);
  color: #2d5a5a;
}

/* ===== 修改个人信息弹窗（弹窗挂在 body 上，写在这里） ===== */
.edit-info-dialog {
  border-radius: 16px;
}

.edit-info-dialog .el-dialog__title {
  color: #2d5a5a;
  font-weight: 600;
}

.edit-type-tabs {
  display: flex;
  justify-content: center;
  margin-bottom: 20px;
}

.edit-info-dialog .edit-info-btn {
  width: 100%;
  background: linear-gradient(135deg, #6abf9e, #4ea8c9) !important;
  border: none !important;
  color: #fff;
}

/* ===== 查看用户信息弹窗（半透明毛玻璃，表格透明化，文字用深色保证看得清） ===== */
.user-list-dialog {
  border-radius: 16px;
  /* 半透明白底 + 背景模糊：透明感有了，深色文字也不会被背景干扰 */
  background: rgba(255, 255, 255, 0.55) !important;
  backdrop-filter: blur(16px) saturate(1.2);
  -webkit-backdrop-filter: blur(16px) saturate(1.2);
}

.user-list-dialog .el-dialog__header {
  border-bottom: 1px solid rgba(255, 255, 255, 0.5);
}

.user-list-dialog .el-dialog__title {
  color: #1e3a3a; /* 深色标题，半透明背景上也清晰 */
  font-weight: 700;
}

/* 表格透明化：把表格自身的白底去掉，露出弹窗的半透明背景 */
.user-list-dialog .el-table {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(255, 255, 255, 0.4);
  --el-table-header-text-color: #1e3a3a;
  --el-table-text-color: #1e3a3a;
  --el-table-border-color: rgba(255, 255, 255, 0.6);
  --el-table-row-hover-bg-color: rgba(106, 191, 158, 0.18);
  font-weight: 500;
}

/* 表头加粗，和内容行区分开 */
.user-list-dialog .el-table th.el-table__cell {
  font-weight: 700;
}
</style>