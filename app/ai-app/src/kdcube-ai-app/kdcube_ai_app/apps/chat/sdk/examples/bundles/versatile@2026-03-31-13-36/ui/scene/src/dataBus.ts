/**
 * Data Bus Socket.IO transport for the scene host.
 *
 * The scene keeps one authenticated socket per runtime and uses it as the
 * scene-level event relay: widgets claim event subscriptions
 * (`kdcube-scene-subscribe`) and the scene re-publishes matching service
 * events to them through the scene event bus. External panels also receive
 * their configured service events through this socket.
 */

import { io, type Socket } from 'socket.io-client'
import { fetchProfileSessionId, type RouteContext } from './sceneConfig'

let dataBusSocket: Socket | null = null
let dataBusSocketKey = ''
let dataBusConnectPromise: Promise<void> | null = null

function resetDataBusSocket(socket: Socket): void {
  if (dataBusSocket === socket) {
    dataBusSocket = null
    dataBusSocketKey = ''
    dataBusConnectPromise = null
  }
}

export async function dataBusSocketFor(ctx: RouteContext): Promise<Socket> {
  const sessionId = await fetchProfileSessionId(ctx)
  const key = `${ctx.baseUrl}|/socket.io|/`
  const auth = {
    user_session_id: sessionId,
    tenant: ctx.tenant,
    project: ctx.project,
    bundle_id: ctx.bundleId,
    ...(ctx.accessToken ? { bearer_token: ctx.accessToken } : {}),
    ...(ctx.idToken ? { id_token: ctx.idToken } : {}),
  }
  if (dataBusSocket && dataBusSocketKey === key) {
    dataBusSocket.auth = auth
    return dataBusSocket
  }
  if (dataBusSocket) {
    dataBusSocket.disconnect()
    dataBusSocket = null
    dataBusConnectPromise = null
  }
  const socket = io(ctx.baseUrl, {
    path: '/socket.io',
    transports: ['websocket'],
    upgrade: false,
    autoConnect: false,
    withCredentials: true,
    auth,
    reconnection: false,
  })
  socket.on('connect_error', (error: Error) => {
    console.warn('[versatile-scene:data-bus] connect_error', { message: error.message })
  })
  socket.on('disconnect', (reason: string) => {
    console.info('[versatile-scene:data-bus] disconnected', { reason })
  })
  dataBusSocket = socket
  dataBusSocketKey = key
  return socket
}

export function ensureSocketConnected(socket: Socket): Promise<void> {
  if (socket.connected) return Promise.resolve()
  if (dataBusConnectPromise) return dataBusConnectPromise
  dataBusConnectPromise = new Promise<void>((resolve, reject) => {
    let timer: number | undefined
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      socket.off('connect', onConnect)
      socket.off('connect_error', onConnectError)
    }
    function onConnect(): void {
      cleanup()
      resolve()
    }
    function onConnectError(error: Error): void {
      cleanup()
      socket.disconnect()
      resetDataBusSocket(socket)
      reject(error)
    }
    timer = window.setTimeout(() => {
      cleanup()
      socket.disconnect()
      resetDataBusSocket(socket)
      reject(new Error('Timed out connecting to the Data Bus Socket.IO transport.'))
    }, 8000)
    socket.once('connect', onConnect)
    socket.once('connect_error', onConnectError)
    socket.connect()
  }).finally(() => {
    dataBusConnectPromise = null
  })
  return dataBusConnectPromise
}
