<script setup lang="ts">
import { reactive } from "vue";

import {
  SECTION_NAMES,
  type Accent,
  type MapHeight,
  type Prefs,
  type RangeMode,
  type SectionName,
  type TextSize,
  type ThemeName,
} from "../prefs";

const sections: { name: SectionName; label: string }[] = [
  { name: "driving", label: "驾驶" },
  { name: "range", label: "续航" },
  { name: "odometer", label: "里程表" },
  { name: "environment", label: "环境" },
  { name: "vehicle", label: "车辆" },
  { name: "segments", label: "分段" },
  { name: "destination", label: "导航目的地" },
];

const accents: { value: Accent; label: string }[] = [
  { value: "blue", label: "蓝" },
  { value: "green", label: "绿" },
  { value: "orange", label: "橙" },
  { value: "purple", label: "紫" },
];

const prefs = reactive<Prefs>(window.teslaslipUI.read());

function fill(next: Prefs): void {
  prefs.theme = next.theme;
  prefs.accent = next.accent;
  prefs.text = next.text;
  prefs.map = next.map;
  prefs.mapHeight = next.mapHeight;
  prefs.rangeMode = next.rangeMode;
  prefs.chargeSamples = next.chargeSamples;
  for (const name of SECTION_NAMES) prefs.sections[name] = next.sections[name];
}

function commit(): void {
  fill(window.teslaslipUI.save(prefs));
}

function setTheme(theme: ThemeName): void {
  prefs.theme = theme;
  commit();
}

function setAccent(accent: Accent): void {
  prefs.accent = accent;
  commit();
}

function setText(text: TextSize): void {
  prefs.text = text;
  commit();
}

function setMapHeight(height: MapHeight): void {
  prefs.mapHeight = height;
  commit();
}

function setRangeMode(mode: RangeMode): void {
  prefs.rangeMode = mode;
  commit();
}

function toggleMap(): void {
  prefs.map = !prefs.map;
  commit();
}

function toggleSection(name: SectionName): void {
  prefs.sections[name] = !prefs.sections[name];
  commit();
}

function toggleSamples(): void {
  prefs.chargeSamples = !prefs.chargeSamples;
  commit();
}

function reset(): void {
  fill(window.teslaslipUI.defaults());
  commit();
}

function back(): void {
  history.back();
}
</script>

<template>
  <div>
    <Teleport to="#settings-back-slot">
      <button type="button" class="theme-toggle" @click="back">返回</button>
    </Teleport>
    <main>
      <section class="group">
        <h2>外观</h2>
        <div class="card">
          <div class="row row-stack">
            <span>主题</span>
            <div class="choice" role="group" aria-label="主题">
              <button type="button" :aria-pressed="prefs.theme === 'system'" @click="setTheme('system')">跟随系统</button>
              <button type="button" :aria-pressed="prefs.theme === 'light'" @click="setTheme('light')">浅色</button>
              <button type="button" :aria-pressed="prefs.theme === 'dark'" @click="setTheme('dark')">深色</button>
            </div>
          </div>
          <div class="row row-stack">
            <span>强调色</span>
            <div class="swatches" role="group" aria-label="强调色">
              <button
                v-for="accent in accents"
                :key="accent.value"
                type="button"
                class="swatch"
                :data-value="accent.value"
                :aria-label="accent.label"
                :aria-pressed="prefs.accent === accent.value"
                @click="setAccent(accent.value)"
              ></button>
            </div>
          </div>
          <div class="row row-stack">
            <span>文字</span>
            <div class="choice" role="group" aria-label="文字">
              <button type="button" :aria-pressed="prefs.text === 'standard'" @click="setText('standard')">标准</button>
              <button type="button" :aria-pressed="prefs.text === 'large'" @click="setText('large')">大</button>
            </div>
          </div>
        </div>
      </section>
      <section class="group">
        <h2>地图</h2>
        <div class="card">
          <div class="row">
            <span>显示地图</span>
            <button type="button" class="switch" :aria-pressed="prefs.map" @click="toggleMap">{{ prefs.map ? "开" : "关" }}</button>
          </div>
          <div class="row row-stack">
            <span>高度</span>
            <div class="choice" role="group" aria-label="地图高度">
              <button type="button" :aria-pressed="prefs.mapHeight === 'compact'" @click="setMapHeight('compact')">紧凑</button>
              <button type="button" :aria-pressed="prefs.mapHeight === 'standard'" @click="setMapHeight('standard')">标准</button>
              <button type="button" :aria-pressed="prefs.mapHeight === 'tall'" @click="setMapHeight('tall')">高</button>
            </div>
          </div>
        </div>
      </section>
      <section class="group">
        <h2>出门页</h2>
        <div class="card">
          <div v-for="section in sections" :key="section.name" class="row">
            <span>{{ section.label }}</span>
            <button type="button" class="switch" :aria-pressed="prefs.sections[section.name]" @click="toggleSection(section.name)">
              {{ prefs.sections[section.name] ? "开" : "关" }}
            </button>
          </div>
        </div>
      </section>
      <section class="group">
        <h2>续航</h2>
        <div class="card">
          <div class="row row-stack">
            <span>口径</span>
            <div class="choice" role="group" aria-label="续航口径">
              <button type="button" :aria-pressed="prefs.rangeMode === 'rated'" @click="setRangeMode('rated')">额定</button>
              <button type="button" :aria-pressed="prefs.rangeMode === 'ideal'" @click="setRangeMode('ideal')">理想</button>
              <button type="button" :aria-pressed="prefs.rangeMode === 'both'" @click="setRangeMode('both')">都显示</button>
            </div>
          </div>
        </div>
      </section>
      <section class="group">
        <h2>充电页</h2>
        <div class="card">
          <div class="row">
            <span>采样明细</span>
            <button type="button" class="switch" :aria-pressed="prefs.chargeSamples" @click="toggleSamples">
              {{ prefs.chargeSamples ? "开" : "关" }}
            </button>
          </div>
        </div>
      </section>
      <section class="group">
        <div class="card">
          <button type="button" class="row reset" @click="reset">恢复默认</button>
        </div>
      </section>
    </main>
  </div>
</template>
