"use strict";
// Redis Browser Admin App (TypeScript)
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
        var identity = 'REDIS_BROWSER_ADMIN';
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
var RedisBrowserAPI = /** @class */ (function () {
    function RedisBrowserAPI(basePath) {
        if (basePath === void 0) { basePath = '/api/admin/control-plane/redis'; }
        this.basePath = basePath;
    }
    RedisBrowserAPI.prototype.buildUrl = function (path) {
        return "".concat(settings.getBaseUrl()).concat(this.basePath).concat(path);
    };
    RedisBrowserAPI.prototype.listKeys = function (prefix, cursor, limit) {
        return __awaiter(this, void 0, void 0, function () {
            var params, res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        params = new URLSearchParams();
                        if (prefix)
                            params.set('prefix', prefix);
                        params.set('cursor', String(cursor));
                        params.set('limit', String(limit));
                        return [4 /*yield*/, fetch(this.buildUrl("/keys?".concat(params.toString())), { headers: makeAuthHeaders() })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load keys');
                        return [4 /*yield*/, res.json()];
                    case 2: return [2 /*return*/, _a.sent()];
                }
            });
        });
    };
    RedisBrowserAPI.prototype.getKey = function (key, maxItems) {
        return __awaiter(this, void 0, void 0, function () {
            var params, res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        params = new URLSearchParams();
                        params.set('key', key);
                        params.set('max_items', String(maxItems));
                        return [4 /*yield*/, fetch(this.buildUrl("/key?".concat(params.toString())), { headers: makeAuthHeaders() })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error('Failed to load key');
                        return [4 /*yield*/, res.json()];
                    case 2: return [2 /*return*/, _a.sent()];
                }
            });
        });
    };
    return RedisBrowserAPI;
}());
var api = new RedisBrowserAPI();
var RedisBrowserAdmin = function () {
    var _a = (0, react_1.useState)(false), configReady = _a[0], setConfigReady = _a[1];
    var _b = (0, react_1.useState)(''), prefix = _b[0], setPrefix = _b[1];
    var _c = (0, react_1.useState)(0), cursor = _c[0], setCursor = _c[1];
    var _d = (0, react_1.useState)([]), keys = _d[0], setKeys = _d[1];
    var _e = (0, react_1.useState)(''), selectedKey = _e[0], setSelectedKey = _e[1];
    var _f = (0, react_1.useState)(''), manualKey = _f[0], setManualKey = _f[1];
    var _g = (0, react_1.useState)(null), keyDetails = _g[0], setKeyDetails = _g[1];
    var _h = (0, react_1.useState)(false), loading = _h[0], setLoading = _h[1];
    var _j = (0, react_1.useState)(null), error = _j[0], setError = _j[1];
    var limit = (0, react_1.useState)(200)[0];
    (0, react_1.useEffect)(function () {
        settings.setupParentListener().then(function () {
            setConfigReady(true);
        });
    }, []);
    var loadKeys = function (reset) { return __awaiter(void 0, void 0, void 0, function () {
        var nextCursor, data_1, err_1;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    if (!configReady)
                        return [2 /*return*/];
                    setLoading(true);
                    setError(null);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    nextCursor = reset ? 0 : cursor;
                    return [4 /*yield*/, api.listKeys(prefix, nextCursor, limit)];
                case 2:
                    data_1 = _a.sent();
                    setCursor(data_1.next_cursor || 0);
                    setKeys(function (prev) { return reset ? data_1.items : __spreadArray(__spreadArray([], prev, true), data_1.items, true); });
                    return [3 /*break*/, 5];
                case 3:
                    err_1 = _a.sent();
                    setError(err_1.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoading(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var loadKeyDetails = function (key) { return __awaiter(void 0, void 0, void 0, function () {
        var data, err_2;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    if (!key)
                        return [2 /*return*/];
                    setLoading(true);
                    setError(null);
                    setSelectedKey(key);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.getKey(key, 200)];
                case 2:
                    data = _a.sent();
                    setKeyDetails(data);
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
    var summary = (0, react_1.useMemo)(function () {
        var _a;
        if (!keyDetails)
            return 'Select a key to inspect.';
        var ttl = keyDetails.ttl === null ? 'n/a' : keyDetails.ttl;
        var len = (_a = keyDetails.length) !== null && _a !== void 0 ? _a : 'n/a';
        return "Type: ".concat(keyDetails.type, " \u2022 TTL: ").concat(ttl, " \u2022 Size: ").concat(len);
    }, [keyDetails]);
    return (<div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-indigo-50">
            <div className="max-w-7xl mx-auto px-6 py-10">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-4xl font-semibold text-gray-900 tracking-tight">Redis Browser</h1>
                        <p className="text-gray-600 mt-2">Explore Redis keys and inspect stored values.</p>
                    </div>
                    <div className="text-sm text-gray-500">{loading ? 'Loading…' : 'Ready'}</div>
                </div>

                {error && (<div className="mb-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700 text-sm">
                        {error}
                    </div>)}

                <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-6">
                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Key filter</div>
                            <input className="w-full rounded-xl border border-gray-200 px-3 py-2 text-xs" placeholder="Prefix (e.g. kdcube:cp:)" value={prefix} onChange={function (e) { return setPrefix(e.target.value); }}/>
                            <div className="flex gap-2 mt-3">
                                <button className="flex-1 px-3 py-2 rounded-xl text-xs font-semibold bg-gray-900 text-white" onClick={function () {
            setCursor(0);
            setKeys([]);
            loadKeys(true);
        }}>
                                    Search
                                </button>
                                <button className="flex-1 px-3 py-2 rounded-xl text-xs font-semibold border border-gray-200 text-gray-700" onClick={function () { return loadKeys(false); }} disabled={cursor === 0 && keys.length > 0}>
                                    Load more
                                </button>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Keys</div>
                            <div className="max-h-72 overflow-auto divide-y divide-gray-100">
                                {keys.map(function (item) {
            var _a;
            return (<button key={item.key} className={"w-full text-left px-3 py-2 text-xs transition ".concat(selectedKey === item.key ? 'bg-indigo-50' : 'hover:bg-gray-50')} onClick={function () { return loadKeyDetails(item.key); }}>
                                        <div className="font-semibold text-gray-900 truncate">{item.key}</div>
                                        <div className="text-gray-500">{item.type} • TTL {(_a = item.ttl) !== null && _a !== void 0 ? _a : 'n/a'}</div>
                                    </button>);
        })}
                                {!keys.length && (<div className="px-3 py-4 text-xs text-gray-500">No keys loaded.</div>)}
                            </div>
                        </div>
                    </div>

                    <div className="space-y-6">
                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="text-sm font-semibold text-gray-900 mb-3">Inspect key</div>
                            <div className="flex gap-2">
                                <input className="flex-1 rounded-xl border border-gray-200 px-3 py-2 text-xs" placeholder="Paste key and press Enter" value={manualKey} onChange={function (e) { return setManualKey(e.target.value); }} onKeyDown={function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                loadKeyDetails(manualKey.trim());
            }
        }}/>
                                <button className="px-3 py-2 rounded-xl text-xs font-semibold bg-indigo-600 text-white" onClick={function () { return loadKeyDetails(manualKey.trim()); }}>
                                    Load
                                </button>
                            </div>
                        </div>

                        <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-3">
                                <div className="text-sm font-semibold text-gray-900">Key data</div>
                                <div className="text-xs text-gray-500">{selectedKey || '—'}</div>
                            </div>
                            <div className="text-xs text-gray-500 mb-3">{summary}</div>
                            <pre className="text-xs bg-gray-900 text-gray-100 rounded-xl p-4 max-h-[420px] overflow-auto">
                                {keyDetails ? JSON.stringify(keyDetails.value, null, 2) : 'Select a key to load details.'}
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
    root.render(<RedisBrowserAdmin />);
}
