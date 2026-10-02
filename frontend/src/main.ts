import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import router from "./router";
import { useAuthStore } from "./stores/auth";
import "./styles.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);

// 作品说明：恢复登录态并注册 401 过期跳转
useAuthStore().init();

app.mount("#app");
