// index.ts
// 购物车绑定 + 商品列表轮询 + 支付 + 订单上传

const POLL_INTERVAL = 3000          // 轮询间隔（毫秒）
const CART_STORAGE_KEY = 'boundCartId'  // 本地缓存 key

interface SnackItem {
  name: string
  weight: number
  unitPrice: string   // 如 "15.80/500g"
  image: string
  totalPrice: string  // 如 "0.40"
}

Page({
  data: {
    // 购物车绑定状态
    cartId: '' as string,
    isBound: false,
    isBinding: false,

    // 商品列表
    snackList: [] as SnackItem[],
    totalAmount: '0.00',
    totalWeight: 0,

    // 支付状态
    isPaying: false,

    // 品牌 logo 云文件 ID，从 config 集合读取
    brandLogo: '' as string,
  },

  _pollTimer: null as ReturnType<typeof setInterval> | null,
  _polling: false,   // 防止轮询请求堆积

  // ── 生命周期 ──────────────────────────────────────────────────────────────

  onLoad() {
    this._fetchBrandLogo()
    // 尝试恢复上次绑定的购物车
    const savedCartId = wx.getStorageSync(CART_STORAGE_KEY) as string
    if (savedCartId) {
      this._validateAndBind(savedCartId, true)
    }
  },

  onUnload() {
    this._stopPolling()
  },

  // ── 轮询控制 ──────────────────────────────────────────────────────────────

  _fetchBrandLogo() {
    wx.cloud.database().collection('config')
      .where({ key: 'brandLogo' })
      .get({
        success: (res: WechatMiniprogram.IQueryResult) => {
          if (res.data.length > 0) {
            this.setData({ brandLogo: (res.data[0] as any).value })
          }
        },
      })
  },

  _startPolling() {
    this._stopPolling()
    this._fetchCartList()   // 立即拉一次
    this._pollTimer = setInterval(() => {
      if (!this._polling) this._fetchCartList()
    }, POLL_INTERVAL)
  },

  _stopPolling() {
    if (this._pollTimer !== null) {
      clearInterval(this._pollTimer)
      this._pollTimer = null
    }
  },

  // ── 扫码绑定购物车 ────────────────────────────────────────────────────────

  onScanTap() {
    if (this.data.isBinding) return
    wx.scanCode({
      onlyFromCamera: false,
      success: (res: WechatMiniprogram.ScanCodeSuccessCallbackResult) => {
        // 二维码内容就是纯购物车 ID，如 "CART001"
        const cartId = (res.result ?? '').trim()
        if (!cartId) {
          wx.showToast({ title: '二维码内容无效', icon: 'error' })
          return
        }
        this._validateAndBind(cartId, false)
      },
      fail: () => {
        // 用户取消扫码，不做任何提示
      },
    })
  },

  // silent=true 时为静默恢复（页面加载时），不弹 toast
  _validateAndBind(cartId: string, silent: boolean) {
    this.setData({ isBinding: true })
    wx.cloud.callFunction({
      name: 'validateCart',
      data: { cartId },
      success: (res: WechatMiniprogram.RequestSuccessCallbackResult) => {
        this.setData({ isBinding: false })
        const result = res.result as any
        if (result?.ok) {
          wx.setStorageSync(CART_STORAGE_KEY, cartId)
          this.setData({ cartId, isBound: true })
          this._startPolling()
          if (!silent) {
            wx.showToast({ title: `已绑定 ${cartId}`, icon: 'success' })
          }
        } else {
          if (!silent) {
            wx.showToast({ title: result?.message ?? '购物车验证失败', icon: 'error' })
          } else {
            // 缓存的 cartId 已失效，清除
            wx.removeStorageSync(CART_STORAGE_KEY)
          }
        }
      },
      fail: () => {
        this.setData({ isBinding: false })
        if (!silent) {
          wx.showToast({ title: '网络异常，请重试', icon: 'error' })
        }
      },
    })
  },

  // 解绑购物车
  onUnbindTap() {
    wx.showModal({
      title: '解绑购物车',
      content: `确认解绑购物车 ${this.data.cartId}？`,
      confirmText: '解绑',
      confirmColor: '#FF5722',
      success: (res: WechatMiniprogram.ShowModalSuccessCallbackResult) => {
        if (res.confirm) {
          this._stopPolling()
          wx.removeStorageSync(CART_STORAGE_KEY)
          this.setData({
            cartId: '',
            isBound: false,
            snackList: [],
            totalAmount: '0.00',
            totalWeight: 0,
          })
        }
      },
    })
  },

  // ── 轮询获取商品列表 ──────────────────────────────────────────────────────

  _fetchCartList() {
    if (!this.data.isBound || !this.data.cartId) return
    this._polling = true
    wx.cloud.callFunction({
      name: 'getCartList',
      data: { cartId: this.data.cartId },
      success: (res: WechatMiniprogram.RequestSuccessCallbackResult) => {
        this._polling = false
        const result = res.result as any
        if (result?.ok) {
          this._applySnackList(result.snackList || [])
        }
      },
      fail: () => {
        this._polling = false
      },
    })
  },

  _applySnackList(snackList: SnackItem[]) {
    const totalWeight = snackList.reduce((sum, item) => sum + item.weight, 0)
    const totalAmount = snackList
      .reduce((sum, item) => sum + parseFloat(item.totalPrice), 0)
      .toFixed(2)
    this.setData({ snackList, totalWeight, totalAmount })
  },

  // ── 支付 ──────────────────────────────────────────────────────────────────

  onPayTap() {
    if (this.data.isPaying || this.data.snackList.length === 0) return
    this.setData({ isPaying: true })
    wx.showLoading({ title: '支付处理中...', mask: true })

    // 模拟支付 1 秒（正式上线替换为 wx.requestPayment）
    setTimeout(() => {
      wx.hideLoading()
      wx.showModal({
        title: '支付成功',
        content: `本次消费 ¥${this.data.totalAmount}，感谢您的购买！`,
        showCancel: false,
        confirmText: '完成',
        success: (res: WechatMiniprogram.ShowModalSuccessCallbackResult) => {
          if (res.confirm) {
            this._uploadOrder()
          }
        },
      })
    }, 1000)
  },

  // 支付成功后上传订单记录，清空本地状态
  _uploadOrder() {
    const { cartId, snackList, totalAmount, totalWeight } = this.data
    wx.cloud.callFunction({
      name: 'completeOrder',
      data: {
        cartId,
        items: snackList,
        totalAmount,
        totalWeight,
      },
      success: (res: WechatMiniprogram.RequestSuccessCallbackResult) => {
        const result = res.result as any
        if (result?.ok) {
          console.log(`订单已上传，orderId=${result.orderId}`)
        } else {
          console.warn('订单上传失败：', result?.message)
        }
      },
      fail: (err: WechatMiniprogram.GeneralCallbackResult) => {
        console.warn('订单上传网络异常：', err.errMsg)
      },
      complete: () => {
        // 无论上传是否成功，都重置本地状态并停止轮询
        // 购物车已被云函数清空，继续轮询会拿到空列表
        this._stopPolling()
        wx.removeStorageSync(CART_STORAGE_KEY)
        this.setData({
          isPaying: false,
          isBound: false,
          cartId: '',
          snackList: [],
          totalAmount: '0.00',
          totalWeight: 0,
        })
      },
    })
  },
})
