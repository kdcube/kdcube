"use strict";
// Conversation Browser Admin App (TypeScript)
var __assign = (this && this.__assign) || function () {
    __assign = Object.assign || function(t) {
        for (var s, i = 1, n = arguments.length; i < n; i++) {
            s = arguments[i];
            for (var p in s) if (Object.prototype.hasOwnProperty.call(s, p))
                t[p] = s[p];
        }
        return t;
    };
    return __assign.apply(this, arguments);
};
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __generator = (this && this.__generator) || function (thisArg, body) {
    var _ = { label: 0, sent: function() { if (t[0] & 1) throw t[1]; return t[1]; }, trys: [], ops: [] }, f, y, t, g = Object.create((typeof Iterator === "function" ? Iterator : Object).prototype);
    return g.next = verb(0), g["throw"] = verb(1), g["return"] = verb(2), typeof Symbol === "function" && (g[Symbol.iterator] = function() { return this; }), g;
    function verb(n) { return function (v) { return step([n, v]); }; }
    function step(op) {
        if (f) throw new TypeError("Generator is already executing.");
        while (g && (g = 0, op[0] && (_ = 0)), _) try {
            if (f = 1, y && (t = op[0] & 2 ? y["return"] : op[0] ? y["throw"] || ((t = y["return"]) && t.call(y), 0) : y.next) && !(t = t.call(y, op[1])).done) return t;
            if (y = 0, t) op = [op[0] & 2, t.value];
            switch (op[0]) {
                case 0: case 1: t = op; break;
                case 4: _.label++; return { value: op[1], done: false };
                case 5: _.label++; y = op[1]; op = [0]; continue;
                case 7: op = _.ops.pop(); _.trys.pop(); continue;
                default:
                    if (!(t = _.trys, t = t.length > 0 && t[t.length - 1]) && (op[0] === 6 || op[0] === 2)) { _ = 0; continue; }
                    if (op[0] === 3 && (!t || (op[1] > t[0] && op[1] < t[3]))) { _.label = op[1]; break; }
                    if (op[0] === 6 && _.label < t[1]) { _.label = t[1]; t = op; break; }
                    if (t && _.label < t[2]) { _.label = t[2]; _.ops.push(op); break; }
                    if (t[2]) _.ops.pop();
                    _.trys.pop(); continue;
            }
            op = body.call(thisArg, _);
        } catch (e) { op = [6, e]; y = 0; } finally { f = t = 0; }
        if (op[0] & 5) throw op[1]; return { value: op[0] ? op[1] : void 0, done: true };
    }
};
var __spreadArray = (this && this.__spreadArray) || function (to, from, pack) {
    if (pack || arguments.length === 2) for (var i = 0, l = from.length, ar; i < l; i++) {
        if (ar || !(i in from)) {
            if (!ar) ar = Array.prototype.slice.call(from, 0, i);
            ar[i] = from[i];
        }
    }
    return to.concat(ar || Array.prototype.slice.call(from));
};
Object.defineProperty(exports, "__esModule", { value: true });
var react_1 = require("react");
var client_1 = require("react-dom/client");
// =============================================================================
// Settings Manager
// =============================================================================
var SettingsManager = /** @class */ (function () {
    function SettingsManager() {
        this.PLACEHOLDER_BASE_URL = '{{' + 'CHAT_BASE_URL' + '}}';
        this.PLACEHOLDER_ACCESS_TOKEN = '{{' + 'ACCESS_TOKEN' + '}}';
        this.PLACEHOLDER_ID_TOKEN = '{{' + 'ID_TOKEN' + '}}';
        this.PLACEHOLDER_ID_TOKEN_HEADER = '{{' + 'ID_TOKEN_HEADER' + '}}';
        this.PLACEHOLDER_TENANT = '{{' + 'DEFAULT_TENANT' + '}}';
        this.PLACEHOLDER_PROJECT = '{{' + 'DEFAULT_PROJECT' + '}}';
        this.PLACEHOLDER_BUNDLE_ID = '{{' + 'DEFAULT_APP_BUNDLE_ID' + '}}';
        this.settings = {
            baseUrl: '{{CHAT_BASE_URL}}',
            accessToken: '{{ACCESS_TOKEN}}',
            idToken: '{{ID_TOKEN}}',
            idTokenHeader: '{{ID_TOKEN_HEADER}}',
            defaultTenant: '{{DEFAULT_TENANT}}',
            defaultProject: '{{DEFAULT_PROJECT}}',
            defaultAppBundleId: '{{DEFAULT_APP_BUNDLE_ID}}'
        };
        this.configReceivedCallback = null;
    }
    SettingsManager.prototype.getBaseUrl = function () {
        if (this.settings.baseUrl === this.PLACEHOLDER_BASE_URL) {
            return 'http://localhost:8010';
        }
        try {
            var url = new URL(this.settings.baseUrl);
            if (url.port === 'None' || url.hostname.includes('None')) {
                return 'http://localhost:8010';
            }
            return this.settings.baseUrl;
        }
        catch (e) {
            return 'http://localhost:8010';
        }
    };
    SettingsManager.prototype.getAccessToken = function () {
        if (this.settings.accessToken === this.PLACEHOLDER_ACCESS_TOKEN || !this.settings.accessToken) {
            return null;
        }
        return this.settings.accessToken;
    };
    SettingsManager.prototype.getIdToken = function () {
        if (this.settings.idToken === this.PLACEHOLDER_ID_TOKEN || !this.settings.idToken) {
            return null;
        }
        return this.settings.idToken;
    };
    SettingsManager.prototype.getIdTokenHeader = function () {
        return this.settings.idTokenHeader === this.PLACEHOLDER_ID_TOKEN_HEADER
            ? 'X-ID-Token'
            : this.settings.idTokenHeader;
    };
    SettingsManager.prototype.getDefaultTenant = function () {
        return this.settings.defaultTenant === this.PLACEHOLDER_TENANT
            ? 'home'
            : this.settings.defaultTenant;
    };
    SettingsManager.prototype.getDefaultProject = function () {
        return this.settings.defaultProject === this.PLACEHOLDER_PROJECT
            ? 'demo'
            : this.settings.defaultProject;
    };
    SettingsManager.prototype.hasPlaceholderSettings = function () {
        return this.settings.baseUrl === this.PLACEHOLDER_BASE_URL;
    };
    SettingsManager.prototype.updateSettings = function (partial) {
        this.settings = __assign(__assign({}, this.settings), partial);
    };
    SettingsManager.prototype.onConfigReceived = function (callback) {
        this.configReceivedCallback = callback;
    };
    SettingsManager.prototype.setupParentListener = function () {
        var _this = this;
        var identity = 'CONVERSATION_BROWSER_ADMIN';
        window.addEventListener('message', function (event) {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                var requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    return;
                }
                if (event.data.config) {
                    var config = event.data.config;
                    var updates = {};
                    if (config.baseUrl && typeof config.baseUrl === 'string') {
                        updates.baseUrl = config.baseUrl;
                    }
                    if (config.accessToken !== undefined) {
                        updates.accessToken = config.accessToken;
                    }
                    if (config.idToken !== undefined) {
                        updates.idToken = config.idToken;
                    }
                    if (config.idTokenHeader) {
                        updates.idTokenHeader = config.idTokenHeader;
                    }
                    if (config.defaultTenant) {
                        updates.defaultTenant = config.defaultTenant;
                    }
                    if (config.defaultProject) {
                        updates.defaultProject = config.defaultProject;
                    }
                    if (config.defaultAppBundleId) {
                        updates.defaultAppBundleId = config.defaultAppBundleId;
                    }
                    if (Object.keys(updates).length > 0) {
                        _this.updateSettings(updates);
                        if (_this.configReceivedCallback) {
                            _this.configReceivedCallback();
                        }
                    }
                }
            }
        });
        if (this.hasPlaceholderSettings()) {
            window.parent.postMessage({
                type: 'CONFIG_REQUEST',
                data: {
                    requestedFields: [
                        'baseUrl', 'accessToken', 'idToken', 'idTokenHeader',
                        'defaultTenant', 'defaultProject', 'defaultAppBundleId'
                    ],
                    identity: identity
                }
            }, '*');
            return new Promise(function (resolve) {
                var timeout = setTimeout(function () {
                    resolve(false);
                }, 3000);
                var originalCallback = _this.configReceivedCallback;
                _this.onConfigReceived(function () {
                    clearTimeout(timeout);
                    if (originalCallback)
                        originalCallback();
                    resolve(true);
                });
            });
        }
        return Promise.resolve(!this.hasPlaceholderSettings());
    };
    return SettingsManager;
}());
var settings = new SettingsManager();
function appendAuthHeaders(headers) {
    var accessToken = settings.getAccessToken();
    var idToken = settings.getIdToken();
    var idTokenHeader = settings.getIdTokenHeader();
    if (accessToken) {
        headers.set('Authorization', "Bearer ".concat(accessToken));
    }
    if (idToken) {
        headers.set(idTokenHeader, idToken);
    }
    return headers;
}
function makeAuthHeaders(base) {
    var headers = new Headers(base);
    return appendAuthHeaders(headers);
}
var ConversationBrowserAPI = /** @class */ (function () {
    function ConversationBrowserAPI(basePath) {
        if (basePath === void 0) { basePath = '/api/admin/control-plane/conversations'; }
        this.basePath = basePath;
    }
    ConversationBrowserAPI.prototype.buildUrl = function (path) {
        return "".concat(settings.getBaseUrl()).concat(this.basePath).concat(path);
    };
    ConversationBrowserAPI.prototype.listTenantProjects = function () {
        return __awaiter(this, void 0, void 0, function () {
            var res, data;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.buildUrl('/tenant-projects'), { headers: makeAuthHeaders() })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load tenant/projects');
                        return [4 /*yield*/, res.json()];
                    case 2:
                        data = _a.sent();
                        return [2 /*return*/, data.items || []];
                }
            });
        });
    };
    ConversationBrowserAPI.prototype.listUsers = function (tenant, project, search) {
        return __awaiter(this, void 0, void 0, function () {
            var params, res, data;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        params = new URLSearchParams();
                        if (search)
                            params.set('search', search);
                        return [4 /*yield*/, fetch(this.buildUrl("/".concat(tenant, "/").concat(project, "/users?").concat(params.toString())), {
                                headers: makeAuthHeaders()
                            })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load users');
                        return [4 /*yield*/, res.json()];
                    case 2:
                        data = _a.sent();
                        return [2 /*return*/, data.items || []];
                }
            });
        });
    };
    ConversationBrowserAPI.prototype.listConversations = function (tenant, project, userId) {
        return __awaiter(this, void 0, void 0, function () {
            var res, data;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.buildUrl("/".concat(tenant, "/").concat(project, "/").concat(userId, "/conversations")), {
                            headers: makeAuthHeaders()
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load conversations');
                        return [4 /*yield*/, res.json()];
                    case 2:
                        data = _a.sent();
                        return [2 /*return*/, data.items || []];
                }
            });
        });
    };
    ConversationBrowserAPI.prototype.getConversationDetails = function (tenant, project, userId, conversationId) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.buildUrl("/".concat(tenant, "/").concat(project, "/").concat(userId, "/conversations/").concat(conversationId, "/details")), { headers: makeAuthHeaders() })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load conversation details');
                        return [4 /*yield*/, res.json()];
                    case 2: return [2 /*return*/, _a.sent()];
                }
            });
        });
    };
    ConversationBrowserAPI.prototype.fetchConversation = function (tenant, project, userId, conversationId) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.buildUrl("/".concat(tenant, "/").concat(project, "/").concat(userId, "/conversations/").concat(conversationId, "/fetch")), {
                            method: 'POST',
                            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({ materialize: true })
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to fetch conversation');
                        return [4 /*yield*/, res.json()];
                    case 2: return [2 /*return*/, _a.sent()];
                }
            });
        });
    };
    ConversationBrowserAPI.prototype.exportUserExcel = function (tenant, project, userId, conversationIds) {
        return __awaiter(this, void 0, void 0, function () {
            var params, suffix, res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        params = new URLSearchParams();
                        if (conversationIds && conversationIds.length) {
                            params.set('conversation_ids', conversationIds.join(','));
                        }
                        suffix = params.toString() ? "?".concat(params.toString()) : '';
                        return [4 /*yield*/, fetch(this.buildUrl("/".concat(tenant, "/").concat(project, "/").concat(userId, "/export.xlsx").concat(suffix)), {
                                headers: makeAuthHeaders()
                            })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to export Excel');
                        return [4 /*yield*/, res.blob()];
                    case 2: return [2 /*return*/, _a.sent()];
                }
            });
        });
    };
    return ConversationBrowserAPI;
}());
var api = new ConversationBrowserAPI();
var ConversationBrowserAdmin = function () {
    var _a;
    var _b = (0, react_1.useState)(false), configReady = _b[0], setConfigReady = _b[1];
    var _c = (0, react_1.useState)([]), tenantProjects = _c[0], setTenantProjects = _c[1];
    var _d = (0, react_1.useState)(settings.getDefaultTenant()), tenant = _d[0], setTenant = _d[1];
    var _e = (0, react_1.useState)(settings.getDefaultProject()), project = _e[0], setProject = _e[1];
    var _f = (0, react_1.useState)(''), userSearch = _f[0], setUserSearch = _f[1];
    var _g = (0, react_1.useState)([]), users = _g[0], setUsers = _g[1];
    var _h = (0, react_1.useState)(''), selectedUser = _h[0], setSelectedUser = _h[1];
    var _j = (0, react_1.useState)([]), conversations = _j[0], setConversations = _j[1];
    var _k = (0, react_1.useState)(''), selectedConversationId = _k[0], setSelectedConversationId = _k[1];
    var _l = (0, react_1.useState)([]), selectedConversationIds = _l[0], setSelectedConversationIds = _l[1];
    var _m = (0, react_1.useState)(''), manualConversationId = _m[0], setManualConversationId = _m[1];
    var _o = (0, react_1.useState)(null), conversationDetails = _o[0], setConversationDetails = _o[1];
    var _p = (0, react_1.useState)(null), conversationFetch = _p[0], setConversationFetch = _p[1];
    var _q = (0, react_1.useState)(false), loading = _q[0], setLoading = _q[1];
    var _r = (0, react_1.useState)(null), error = _r[0], setError = _r[1];
    var tenantProjectOptions = (0, react_1.useMemo)(function () { return tenantProjects.map(function (tp) { return ({
        value: "".concat(tp.tenant, "::").concat(tp.project),
        label: "".concat(tp.tenant, " / ").concat(tp.project),
        item: tp
    }); }); }, [tenantProjects]);
    (0, react_1.useEffect)(function () {
        settings.setupParentListener().then(function () {
            setTenant(settings.getDefaultTenant());
            setProject(settings.getDefaultProject());
            setConfigReady(true);
        });
    }, []);
    (0, react_1.useEffect)(function () {
        if (!configReady)
            return;
        api.listTenantProjects()
            .then(setTenantProjects)
            .catch(function (err) { return setError(err.message); });
    }, [configReady]);
    (0, react_1.useEffect)(function () {
        if (!configReady || !tenant || !project)
            return;
        setLoading(true);
        setError(null);
        setSelectedUser('');
        setUsers([]);
        setConversations([]);
        setSelectedConversationId('');
        setSelectedConversationIds([]);
        setManualConversationId('');
        setConversationDetails(null);
        setConversationFetch(null);
        api.listUsers(tenant, project, userSearch)
            .then(setUsers)
            .catch(function (err) { return setError(err.message); })
            .finally(function () { return setLoading(false); });
    }, [tenant, project, userSearch, configReady]);
    (0, react_1.useEffect)(function () {
        if (!selectedUser || !tenant || !project)
            return;
        setLoading(true);
        setError(null);
        setConversations([]);
        setSelectedConversationId('');
        setSelectedConversationIds([]);
        setManualConversationId('');
        setConversationDetails(null);
        setConversationFetch(null);
        api.listConversations(tenant, project, selectedUser)
            .then(setConversations)
            .catch(function (err) { return setError(err.message); })
            .finally(function () { return setLoading(false); });
    }, [selectedUser, tenant, project]);
    var loadConversation = function (conversationId) { return __awaiter(void 0, void 0, void 0, function () {
        var _a, details, fetched, err_1;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    if (!selectedUser)
                        return [2 /*return*/];
                    setLoading(true);
                    setError(null);
                    setSelectedConversationId(conversationId);
                    _b.label = 1;
                case 1:
                    _b.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, Promise.all([
                            api.getConversationDetails(tenant, project, selectedUser, conversationId),
                            api.fetchConversation(tenant, project, selectedUser, conversationId)
                        ])];
                case 2:
                    _a = _b.sent(), details = _a[0], fetched = _a[1];
                    setConversationDetails(details);
                    setConversationFetch(fetched);
                    return [3 /*break*/, 5];
                case 3:
                    err_1 = _b.sent();
                    setError(err_1.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoading(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var downloadExcel = function () { return __awaiter(void 0, void 0, void 0, function () {
        var blob, url, link, err_2;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    if (!selectedUser)
                        return [2 /*return*/];
                    setLoading(true);
                    setError(null);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.exportUserExcel(tenant, project, selectedUser, selectedConversationIds.length ? selectedConversationIds : undefined)];
                case 2:
                    blob = _a.sent();
                    url = URL.createObjectURL(blob);
                    link = document.createElement('a');
                    link.href = url;
                    link.download = "".concat(selectedUser, "_conversations.xlsx");
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    URL.revokeObjectURL(url);
                    return [3 /*break*/, 5];
                case 3:
                    err_2 = _a.sent();
                    setError(err_2.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoading(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var addConversationId = function (conversationId) {
        var trimmed = conversationId.trim();
        if (!trimmed)
            return;
        setSelectedConversationIds(function (prev) {
            if (prev.includes(trimmed))
                return prev;
            return __spreadArray(__spreadArray([], prev, true), [trimmed], false);
        });
    };
    var removeConversationId = function (conversationId) {
        setSelectedConversationIds(function (prev) { return prev.filter(function (cid) { return cid !== conversationId; }); });
    };
    var toggleConversationId = function (conversationId) {
        setSelectedConversationIds(function (prev) { return (prev.includes(conversationId)
            ? prev.filter(function (cid) { return cid !== conversationId; })
            : __spreadArray(__spreadArray([], prev, true), [conversationId], false)); });
    };
    return (<div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
            <div className="max-w-7xl mx-auto px-6 py-10">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-4xl font-semibold text-gray-900 tracking-tight">Conversation Browser</h1>
                        <p className="text-gray-600 mt-2">Inspect user conversations across tenant projects.</p>
                    </div>
                    <div className="text-sm text-gray-500">
                        {loading ? 'Loading…' : 'Ready'}
                    </div>
                </div>

                {error && (<div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700 text-sm">
                        {error}
                    </div>)}

                <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Scope</div>
                            <div className="space-y-3">
                                <label className="block text-xs font-semibold text-gray-600">Tenant / Project</label>
                                <select className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm" value={"".concat(tenant, "::").concat(project)} onChange={function (e) {
            var _a = e.target.value.split('::'), t = _a[0], p = _a[1];
            setTenant(t || '');
            setProject(p || '');
        }}>
                                    {tenantProjectOptions.map(function (opt) { return (<option key={opt.value} value={opt.value}>{opt.label}</option>); })}
                                </select>
                                <div className="grid grid-cols-2 gap-2">
                                    <input className="rounded-xl border border-gray-200 px-3 py-2 text-xs" value={tenant} onChange={function (e) { return setTenant(e.target.value); }} placeholder="Tenant"/>
                                    <input className="rounded-xl border border-gray-200 px-3 py-2 text-xs" value={project} onChange={function (e) { return setProject(e.target.value); }} placeholder="Project"/>
                                </div>
                                <p className="text-xs text-gray-500">Schema: {tenant && project ? "".concat(tenant, "_").concat(project) : '—'}</p>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Users</div>
                                <button className="text-xs text-blue-600 font-semibold" onClick={function () { return api.listUsers(tenant, project, userSearch).then(setUsers); }}>
                                    Refresh
                                </button>
                            </div>
                            <input className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs mb-3" placeholder="Search users" value={userSearch} onChange={function (e) { return setUserSearch(e.target.value); }}/>
                            <div className="max-h-72 overflow-auto space-y-2">
                                {users.map(function (user) { return (<button key={user} className={"w-full text-left px-3 py-2 rounded-xl text-xs font-semibold transition ".concat(selectedUser === user ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200')} onClick={function () { return setSelectedUser(user); }}>
                                        {user}
                                    </button>); })}
                                {!users.length && (<div className="text-xs text-gray-500">No users found.</div>)}
                            </div>
                            <button className="mt-4 w-full px-4 py-2 rounded-xl text-sm font-semibold bg-gray-900 text-white disabled:opacity-50" onClick={downloadExcel} disabled={!selectedUser}>
                                Download Excel for User
                            </button>
                        </div>
                    </div>

                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Conversations</div>
                                <div className="text-xs text-gray-500">{selectedUser || 'Select a user'}</div>
                            </div>
                            <div className="max-h-52 overflow-auto divide-y divide-gray-100">
                                {conversations.map(function (conv) { return (<div key={conv.conversation_id} className={"flex items-center justify-between px-3 py-2 text-xs transition ".concat(selectedConversationId === conv.conversation_id ? 'bg-blue-50' : 'hover:bg-gray-50')}>
                                        <button onClick={function () { return loadConversation(conv.conversation_id); }} className="flex-1 text-left">
                                            <div className="font-semibold text-gray-900">
                                                {conv.title || conv.conversation_id}
                                            </div>
                                            <div className="text-gray-500">
                                                {conv.last_activity_at || conv.started_at || '—'}
                                            </div>
                                        </button>
                                        <button className={"ml-3 px-2 py-1 rounded-lg border text-[10px] font-semibold ".concat(selectedConversationIds.includes(conv.conversation_id) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-700 border-gray-200')} onClick={function () { return toggleConversationId(conv.conversation_id); }}>
                                            {selectedConversationIds.includes(conv.conversation_id) ? 'Added' : 'Add'}
                                        </button>
                                    </div>); })}
                                {!conversations.length && (<div className="px-3 py-4 text-xs text-gray-500">No conversations loaded.</div>)}
                            </div>
                            <div className="mt-4 border-t border-gray-100 pt-4">
                                <div className="text-xs font-semibold text-gray-700 mb-2">Report selection</div>
                                <input className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs" placeholder="Paste conversation id and press Enter" value={manualConversationId} onChange={function (e) { return setManualConversationId(e.target.value); }} onKeyDown={function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                addConversationId(manualConversationId);
                setManualConversationId('');
            }
        }}/>
                                <div className="flex flex-wrap gap-2 mt-3">
                                    {selectedConversationIds.map(function (cid) { return (<span key={cid} className="inline-flex items-center gap-2 px-2 py-1 rounded-full bg-blue-50 text-[11px] font-semibold text-blue-700 border border-blue-100">
                                            <span className="truncate max-w-[180px]">{cid}</span>
                                            <button className="text-blue-700 hover:text-blue-900" onClick={function () { return removeConversationId(cid); }}>
                                                x
                                            </button>
                                        </span>); })}
                                    {!selectedConversationIds.length && (<span className="text-[11px] text-gray-400">No conversations selected.</span>)}
                                </div>
                                <div className="text-[11px] text-gray-400 mt-2">
                                    {selectedConversationIds.length ? "".concat(selectedConversationIds.length, " selected") : 'Exporting with no selections includes all conversations.'}
                                </div>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Conversation JSON</div>
                                <div className="text-xs text-gray-500">{selectedConversationId || '—'}</div>
                            </div>
                            {conversationDetails && (<div className="text-xs text-gray-500 mb-3">
                                    Turns: {((_a = conversationDetails.turns) === null || _a === void 0 ? void 0 : _a.length) || 0}
                                </div>)}
                            <pre className="text-xs bg-gray-900 text-gray-100 rounded-xl p-4 max-h-[420px] overflow-auto">
                                {conversationFetch ? JSON.stringify(conversationFetch, null, 2) : 'Select a conversation to load JSON.'}
                            </pre>
                        </div>
                    </div>
                </div>
            </div>
        </div>);
};
var rootElement = document.getElementById('root');
if (rootElement) {
    var root = client_1.default.createRoot(rootElement);
    root.render(<ConversationBrowserAdmin />);
}
