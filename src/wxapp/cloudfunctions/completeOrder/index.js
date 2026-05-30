// 支付完成后由小程序调用，将订单写入 orders 集合，并清空购物车状态
// 入参：{ cartId, items, totalAmount, totalWeight }
// openid 由云函数从调用上下文自动获取，无需前端传递

const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

exports.main = async (event, context) => {
  const { cartId, items, totalAmount, totalWeight } = event

  // ── 参数校验 ──────────────────────────────────────────────────────────────
  if (!cartId || typeof cartId !== 'string' || cartId.trim() === '') {
    return { ok: false, code: 'MISSING_CART_ID', message: 'cartId 不能为空' }
  }
  if (!Array.isArray(items) || items.length === 0) {
    return { ok: false, code: 'INVALID_ITEMS', message: 'items 不能为空' }
  }
  if (typeof totalAmount !== 'string' || isNaN(parseFloat(totalAmount))) {
    return { ok: false, code: 'INVALID_AMOUNT', message: 'totalAmount 格式错误' }
  }
  if (typeof totalWeight !== 'number' || totalWeight <= 0) {
    return { ok: false, code: 'INVALID_WEIGHT', message: 'totalWeight 必须是正数' }
  }

  const cid = cartId.trim()

  // openid 从云函数调用上下文中安全获取，防止前端伪造
  const openid = context.OPENID || ''

  const now = new Date()

  // ── 写入订单记录 ──────────────────────────────────────────────────────────
  const orderRes = await db.collection('orders').add({
    data: {
      cartId: cid,
      openid,
      items,                              // 完整商品快照，含名称/重量/单价/小计
      totalAmount: parseFloat(totalAmount),
      totalWeight,
      createdAt: now,
    },
  })

  // ── 清空购物车状态（删除 carts 集合中该购物车的记录）────────────────────
  // 使用 where + remove 而非按 _id 删除，因为 carts 记录由机器创建，_id 不在前端
  await db.collection('carts').where({ cartId: cid }).remove()

  console.log(`[completeOrder] 购物车 ${cid} 订单已保存，_id=${orderRes._id}，金额 ¥${totalAmount}`)
  return {
    ok: true,
    orderId: orderRes._id,
    cartId: cid,
    totalAmount,
  }
}
