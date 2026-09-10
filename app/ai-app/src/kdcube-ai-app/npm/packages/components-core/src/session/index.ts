/**
 * @kdcube/components-core/session: the browser side of one server-held
 * session. A surface (a site shell, a widget, an application page) needs
 * exactly this: probe `/profile`, send the browser to the sign-in with a
 * same-origin `next`, post the sign-out, and hear `kdcube-auth-changed`.
 * No token reaches JavaScript; the cookie is HttpOnly and the server slides it.
 */
export * from './types'
export * from './session'
