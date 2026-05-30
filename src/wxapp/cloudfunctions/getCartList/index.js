// 小程序轮询调用，获取指定购物车的当前商品列表
// 入参：{ cartId: string }
// 返回：{ ok: true, snackList: [...], updatedAt: string }

const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

exports.main = async (event) => {
  const { cartId } = event

  if (!cartId || typeof cartId !== 'string' || cartId.trim() === '') {
    return { ok: false, code: 'MISSING_CART_ID', message: 'cartId 不能为空' }
  }

  const cid = cartId.trim()
  const res = await db.collection('carts')
    .where({ cartId: cid })
    .field({ snackList: true, updatedAt: true })
    .get()

  if (res.data.length === 0) {
    // 购物车存在但还没有数据，返回空列表
    return { ok: true, cartId: cid, snackList: [], updatedAt: null }
  }

  const cart = res.data[0]
  return {
    ok: true,
    cartId: cid,
    snackList: cart.snackList || [],
    updatedAt: cart.updatedAt,
  }
}
