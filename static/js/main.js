/**
 * ALGOTEST 前端主脚本文件
 * 包含通用功能和工具函数
 */

// 通用状态映射
const STATUS_MAP = {
    'created': '已创建',
    'preparing': '准备中',
    'executing': '执行中',
    'completed': '已完成',
    'failed': '失败',
    'analyzing': '分析中',
    'analyzed': '已分析'
};

// 通用状态样式映射
const STATUS_CLASS_MAP = {
    'created': 'bg-secondary',
    'preparing': 'bg-info',
    'executing': 'bg-primary',
    'completed': 'bg-success',
    'failed': 'bg-danger',
    'analyzing': 'bg-info',
    'analyzed': 'bg-success'
};

/**
 * 获取状态显示文本
 * @param {string} status - 状态代码
 * @returns {string} - 状态显示文本
 */
function getStatusText(status) {
    return STATUS_MAP[status] || status;
}

/**
 * 获取状态徽章样式类
 * @param {string} status - 状态代码
 * @returns {string} - 样式类名
 */
function getStatusBadgeClass(status) {
    return STATUS_CLASS_MAP[status] || 'bg-secondary';
}

/**
 * 格式化日期时间
 * @param {string} dateString - ISO日期字符串
 * @returns {string} - 格式化后的日期时间字符串
 */
function formatDate(dateString) {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * 显示加载指示器
 * @param {HTMLElement} container - 容器元素
 * @param {string} [message='加载中...'] - 加载消息
 */
function showLoading(container, message = '加载中...') {
    container.innerHTML = `
        <div class="text-center my-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">${message}</p>
        </div>
    `;
}

/**
 * 显示错误消息
 * @param {HTMLElement} container - 容器元素
 * @param {string} message - 错误消息
 */
function showError(container, message) {
    container.innerHTML = `
        <div class="alert alert-danger" role="alert">
            <i class="bi bi-exclamation-triangle-fill me-2"></i>
            ${message}
        </div>
    `;
}

/**
 * 发送API请求
 * @param {string} url - API地址
 * @param {Object} options - 请求选项
 * @returns {Promise} - Fetch Promise
 */
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(url, options);
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `HTTP error ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API请求失败:', error);
        throw error;
    }
}

// 初始化代码
document.addEventListener('DOMContentLoaded', function() {
    // 处理移动视图下的侧边栏折叠
    const sidebarToggle = document.querySelector('.navbar-toggler');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            document.querySelector('.sidebar').classList.toggle('show');
        });
    }
}); 