// 机器调用此云函数上报称重数据
// 入参：{ cartId: string, items: [{ name: string, weight: number }] }
// 逻辑：按商品名查 products 集合获取单价，计算小计，写入 carts 集合
// 机器通过云函数 HTTP 触发器调用（需在云控制台开启 URL 化）

const cloud = require('wx-server-sdk')
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV })
const db = cloud.database()

exports.main = async (event) => {
  const { cartId, items } = event

  // ── 参数校验 ──────────────────────────────────────────────────────────────
  if (!cartId || typeof cartId !== 'string' || cartId.trim() === '') {
    return { ok: false, code: 'MISSING_CART_ID', message: 'cartId 不能为空' }
  }
  if (!Array.isArray(items) || items.length === 0) {
    return { ok: false, code: 'INVALID_ITEMS', message: 'items 必须是非空数组' }
  }
  for (const item of items) {
    if (!item.name || typeof item.name !== 'string') {
      return { ok: false, code: 'INVALID_ITEM', message: `商品 name 字段缺失或格式错误` }
    }
    if (typeof item.weight !== 'number' || item.weight <= 0) {
      return { ok: false, code: 'INVALID_WEIGHT', message: `商品 ${item.name} 的 weight 必须是正数（单位：克）` }
    }
  }

  const cid = cartId.trim()

  // ── 批量查询商品单价 ───────────────────────────────────────────────────────
  // 一次查出所有涉及的商品，减少数据库往返次数
  const nameList = [...new Set(items.map(i => i.name))]
  const productRes = await db.collection('products')
    .where({ name: db.command.in(nameList) })
    .field({ name: true, unitPrice: true, image: true })
    .get()

  // 建立 name -> product 的映射
  const productMap = {}
  for (const p of productRes.data) {
    productMap[p.name] = p
  }

  // 检查是否有未录入数据库的商品
  const missing = nameList.filter(n => !productMap[n])
  if (missing.length > 0) {
    return {
      ok: false,
      code: 'PRODUCT_NOT_FOUND',
      message: `以下商品未在数据库中找到，请先录入：${missing.join('、')}`,
    }
  }

  // ── 计算每条商品的小计 ────────────────────────────────────────────────────
  const snackList = items.map(item => {
    const product = productMap[item.name]
    // unitPrice 存的是每500g的价格（元）
    const totalPrice = ((item.weight * product.unitPrice) / 500).toFixed(2)
    return {
      name: item.name,
      weight: item.weight,
      unitPrice: `${product.unitPrice.toFixed(2)}/500g`,
      image: product.image || '',
      totalPrice,
    }
  })

  // ── 写入 carts 集合（upsert：先 update，若未命中再 add，避免 count+add 竞态）
  const now = new Date()
  const cartCol = db.collection('carts')
  const updateRes = await cartCol.where({ cartId: cid }).update({
    data: {
      snackList,
      updatedAt: now,
    },
  })

  if (updateRes.stats.updated === 0) {
    // 记录不存在，新建；若并发导致重复插入，后续 update 仍能覆盖，影响可控
    await cartCol.add({
      data: {
        cartId: cid,
        snackList,
        createdAt: now,
        updatedAt: now,
      },
    })
  }

  console.log(`[machineWeigh] 购物车 ${cid} 更新，共 ${snackList.length} 种商品`)
  return { ok: true, cartId: cid, count: snackList.length }
}
