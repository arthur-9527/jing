# HtmlPanel 使用说明

## 概述
`HtmlPanel` 是一个叠加在 3D 场景上的 HTML 面板组件，支持**多面板同时显示**，通过 `eventBus` 事件系统控制。面板采用科幻风格设计，带有角落装饰、发光边框和开关闭合动画效果。

## 核心特性
- ✅ **多面板支持** - 可同时显示多个独立面板，每个面板有唯一 `id`
- ✅ **科幻风格样式** - 深色半透明背景、发光边框、角落装饰
- ✅ **动画效果** - 打开/关闭时有缩放和透明度动画
- ✅ **3D 深度效果** - 根据 `z` 值自动调整面板缩放
- ✅ **自动关闭** - 支持 `duration` 参数设置自动关闭时间
- ✅ **事件驱动** - 通过 `eventBus` 事件系统控制

## 组件位置
- 组件文件：`src/three/HtmlPanel.tsx`
- 场景挂载：`src/three/Scene.tsx`

---

## 类型定义

### HtmlPanelState

```typescript
type HtmlPanelState = {
    id: string;         // 必填：面板唯一标识符
    visible: boolean;   // 必填：是否显示面板
    x: number;          // 必填：X 坐标（像素）
    y: number;          // 必填：Y 坐标（像素）
    z: number;          // 必填：Z 深度值（影响缩放，z 越小面板越小/越远）
    width: number;      // 必填：面板宽度（像素）
    height: number;     // 必填：面板高度（像素）
    html: string;       // 必填：要渲染的 HTML 内容
    opacity?: number;   // 可选：透明度（0-1，默认 1）
    zIndex?: number;    // 可选：CSS z-index 层级
    duration?: number;  // 可选：自动关闭时间（毫秒），不设置则不自动关闭
};
```

### 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | string | ✅ | - | 面板唯一标识，用于后续更新或关闭面板 |
| `visible` | boolean | ✅ | - | `true` 显示/更新面板，`false` 关闭面板 |
| `x` | number | ✅ | - | 面板左上角 X 坐标（像素） |
| `y` | number | ✅ | - | 面板左上角 Y 坐标（像素） |
| `z` | number | ✅ | - | 3D 深度值，影响缩放效果（`scale = 1 - z * 0.05`） |
| `width` | number | ✅ | - | 面板宽度（像素） |
| `height` | number | ✅ | - | 面板高度（像素） |
| `html` | string | ✅ | - | 要渲染的 HTML 字符串内容 |
| `opacity` | number | ❌ | 1 | 面板透明度，范围 0-1 |
| `zIndex` | number | ❌ | - | CSS z-index，用于控制面板叠加顺序 |
| `duration` | number | ❌ | - | 自动关闭时间（毫秒），不设置则需手动关闭 |

---

## 坐标系详解

### X, Y 坐标 - 屏幕像素坐标系

HtmlPanel 使用 **视口（Viewport）坐标系**，与 CSS 的 `position: absolute` 定位一致：

```
┌──────────────────────────────────────┐
│ (0, 0)                               │
│    ────► X 轴（向右为正）              │
│    │                                 │
│    │                                 │
│    ▼ Y 轴（向下为正）                  │
│                                      │
│                                      │
│                           (x, y) = 面板左上角位置
│                                      │
└──────────────────────────────────────┘
```

- **原点 (0, 0)**：视口（浏览器窗口）**左上角**
- **X 轴**：向右为正方向，单位为像素
- **Y 轴**：向下为正方向，单位为像素
- **定位方式**：面板左上角对齐到 (x, y) 坐标点

**示例**：
```typescript
x: 100,  // 面板左上角距离视口左边 100 像素
y: 50,   // 面板左上角距离视口顶部 50 像素
```

### Z 坐标 - 模拟深度值

`z` 参数 **不是真正的 3D 空间坐标**，而是一个 **缩放系数**，用于模拟透视深度效果：

- **公式**：`scale = 1 - z * 0.05`
- **取值建议**：0 ~ 10（超过 10 会导致面板过小甚至反向缩放）

| z 值 | scale 计算 | 缩放效果 | 视觉感受 |
|------|-----------|---------|---------|
| 0 | 1 - 0×0.05 = 1.0 | 100%（原始大小） | 最近/前景 |
| 1 | 1 - 1×0.05 = 0.95 | 95% | 较近 |
| 2 | 1 - 2×0.05 = 0.90 | 90% | 中等 |
| 5 | 1 - 5×0.05 = 0.75 | 75% | 较远 |
| 10 | 1 - 10×0.05 = 0.50 | 50% | 最远/背景 |

**注意**：`z` 值只影响缩放，不影响面板的 X/Y 位置或遮挡关系。如需控制叠加顺序，请使用 `zIndex` 参数。

---

## HTML 内容说明

### 直接传 HTML 字符串

`html` 参数接收一个 **HTML 字符串**，内部使用 React 的 `dangerouslySetInnerHTML` 进行渲染：

```typescript
// ✅ 正确用法：直接传入 HTML 字符串
html: '<div style="color: #00d4ff; font-size: 16px;">Hello World</div>'

// ✅ 支持多行 HTML
html: `
  <h3 style="margin: 0; color: #00d4ff;">标题</h3>
  <p style="color: #88ccff;">内容描述</p>
  <ul>
    <li>列表项 1</li>
    <li>列表项 2</li>
  </ul>
`

// ✅ 支持内联样式
html: '<div style="padding: 10px; background: rgba(0,50,100,0.5);">带样式的内容</div>'
```

### 安全警告

⚠️ `html` 参数使用 `dangerouslySetInnerHTML` 渲染，请确保：
1. **内容来源可信**：不要渲染用户输入或未经验证的内容
2. **防止 XSS 攻击**：避免注入恶意脚本
3. **样式隔离**：建议使用内联样式，避免全局样式污染

---

## 事件 API

HtmlPanel 通过 `eventBus` 监听以下事件：

### 1. `panel:html` - 显示/更新/隐藏面板

```typescript
import { eventBus } from '../utils/eventBus';

// 显示新面板
eventBus.emit('panel:html', {
    id: 'panel-1',
    visible: true,
    x: 100,
    y: 100,
    z: 1,
    width: 300,
    height: 200,
    html: '<div style="color: #00d4ff;">Hello World</div>',
    opacity: 0.9,
    zIndex: 10,
    duration: 5000  // 5秒后自动关闭
});

// 更新现有面板（使用相同 id）
eventBus.emit('panel:html', {
    id: 'panel-1',
    visible: true,
    x: 150,
    y: 150,
    z: 2,
    width: 350,
    height: 250,
    html: '<div>Updated Content</div>'
});

// 关闭面板（方式一）
eventBus.emit('panel:html', {
    id: 'panel-1',
    visible: false
});
```

### 2. `panel:close` - 关闭指定面板

```typescript
import { eventBus } from '../utils/eventBus';

// 关闭指定 id 的面板（带关闭动画）
eventBus.emit('panel:close', { id: 'panel-1' });
```

---

## 使用示例

### 示例 1：显示简单面板

```typescript
import { eventBus } from '../utils/eventBus';

eventBus.emit('panel:html', {
    id: 'info-panel',
    visible: true,
    x: 50,
    y: 50,
    z: 0,
    width: 280,
    height: 160,
    html: `
        <h3 style="margin: 0 0 10px 0; color: #00d4ff;">系统信息</h3>
        <p style="margin: 0; color: #88ccff;">状态：运行中</p>
        <p style="margin: 5px 0 0 0; color: #88ccff;">版本：v1.0.0</p>
    `
});
```

### 示例 2：自动关闭的提示面板

```typescript
eventBus.emit('panel:html', {
    id: 'notification',
    visible: true,
    x: 200,
    y: 100,
    z: 0,
    width: 320,
    height: 80,
    html: '<div style="text-align: center; padding: 20px;">操作成功！</div>',
    duration: 3000  // 3秒后自动关闭
});
```

### 示例 3：多面板同时显示

```typescript
// 显示第一个面板
eventBus.emit('panel:html', {
    id: 'panel-left',
    visible: true,
    x: 20,
    y: 50,
    z: 1,
    width: 200,
    height: 300,
    html: '<div>左侧面板</div>'
});

// 显示第二个面板
eventBus.emit('panel:html', {
    id: 'panel-right',
    visible: true,
    x: 500,
    y: 50,
    z: 1,
    width: 200,
    height: 300,
    html: '<div>右侧面板</div>'
});
```

### 示例 4：使用 3D 深度效果

```typescript
// 前景面板（z=0，无缩放）
eventBus.emit('panel:html', {
    id: 'foreground',
    visible: true,
    x: 100,
    y: 100,
    z: 0,  // scale = 1
    width: 300,
    height: 200,
    html: '<div>前景内容</div>'
});

// 背景面板（z=5，缩小 25%）
eventBus.emit('panel:html', {
    id: 'background',
    visible: true,
    x: 400,
    y: 100,
    z: 5,  // scale = 1 - 5 * 0.05 = 0.75
    width: 300,
    height: 200,
    html: '<div>背景内容</div>'
});
```

---

## 样式说明

### 默认科幻风格

面板默认采用科幻风格样式：

```css
/* 容器样式 */
background: rgba(0, 10, 30, 0.92);    /* 深蓝色半透明背景 */
color: #00d4ff;                         /* 青色文字 */
border: 1px solid rgba(0, 180, 255, 0.7); /* 发光边框 */
box-shadow: 0 0 20px rgba(0, 180, 255, 0.3), 
            inset 0 0 30px rgba(0, 100, 200, 0.05); /* 发光效果 */
font-family: Consolas, "Courier New", monospace; /* 等宽字体 */
```

### 装饰元素

- **角落装饰**：四个角落有 2px 的青色边框装饰
- **顶部/底部装饰线**：渐变透明的装饰线
- **内容区域**：无默认内边距（`padding: 0`），超出内容会被裁剪（`overflow: hidden`），用户需在 HTML 中自行控制内边距

---

## 动画效果

### 打开动画
- **时长**：300ms
- **效果**：从 `scale(0.3)` 放大到 `scale(1)`，同时透明度从 0 到 1
- **缓动函数**：`ease-out`

### 关闭动画
- **时长**：300ms  
- **效果**：从 `scale(1)` 缩小到 `scale(0.8)`，同时透明度从 1 到 0
- **缓动函数**：`ease-in`

---

## 注意事项

1. **坐标系**：位置 `(x, y)` 基于视口左上角，单位为像素
2. **HTML 安全**：`html` 字段使用 `dangerouslySetInnerHTML` 渲染，请确保内容安全
3. **面板覆盖**：相同 `id` 的面板会被新内容覆盖更新
4. **内存管理**：面板关闭时会自动清理相关定时器
5. **交互性**：面板默认 `pointerEvents: none`，不可交互，仅用于展示

---

## 完整代码示例

```typescript
import { eventBus } from '../utils/eventBus';

// 显示一个带格式的信息面板
function showInfoPanel(title: string, content: string) {
    eventBus.emit('panel:html', {
        id: 'info-panel',
        visible: true,
        x: 50,
        y: 50,
        z: 0,
        width: 350,
        height: 200,
        html: `
            <div style="padding: 10px;">
                <h2 style="margin: 0 0 15px 0; color: #00d4ff; border-bottom: 1px solid rgba(0, 180, 255, 0.5); padding-bottom: 10px;">
                    ${title}
                </h2>
                <div style="color: #88ccff; line-height: 1.6;">
                    ${content}
                </div>
            </div>
        `,
        duration: 8000  // 8秒后自动关闭
    });
}

// 关闭面板
function closeInfoPanel() {
    eventBus.emit('panel:close', { id: 'info-panel' });
}