// app.ts
App<IAppOption>({
  globalData: {},
  onLaunch() {
    // 初始化云开发，替换 YOUR_ENV_ID 为你的云开发环境 ID
    wx.cloud.init({
      env: 'YOUR_ENV_ID',
      traceUser: true,
    })
  },
})
