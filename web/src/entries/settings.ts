import { createApp } from "vue";

import SettingsApp from "../components/SettingsApp.vue";

const el = document.getElementById("settings-app");
if (el) createApp(SettingsApp).mount(el);
