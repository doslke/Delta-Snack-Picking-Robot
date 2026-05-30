// 小程序扫码后调用，验证购物车 ID 是否合法
// 入参：{ cartId: string }
// 逻辑：格式校验 + 检查 carts 集合中是否已有该购物车记录
// 返回：{ ok: true, cartId, hasItems, itemCount }

const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

// 购物车 ID 格式：字母、数字、下划线、连字符，3-20 位
const CART_ID_PATTERN = /^[A-Za-z0-9_-]{3,20}$/

exports.main = async (event) => {
  const { cartId } = event

  if (!cartId || typeof cartId !== 'string' || cartId.trim() === '') {
    return { ok: false, code: 'MISSING_CART_ID', message: 'cartId 不能为空' }
  }

  const cid = cartId.trim()

  if (!CART_ID_PATTERN.test(cid)) {
    return {
      ok: false,
      code: 'INVALID_FORMAT',
      message: '二维码格式不正确，请扫描购物车上的专属二维码',
    }
  }

  // 查询该购物车是否已有数据（机器是否已上报过）
  const res = await db.collection('carts')
    .where({ cartId: cid })
    .field({ snackList: true })
    .get()

  const hasRecord = res.data.length > 0
  const itemCount = hasRecord ? (res.data[0].snackList || []).length : 0

  console.log(`[validateCart] 购物车 ${cid} 验证通过，hasRecord=${hasRecord}，itemCount=${itemCount}`)
  return {
    ok: true,
    cartId: cid,
    hasItems: itemCount > 0,
    itemCount,
  }
}
