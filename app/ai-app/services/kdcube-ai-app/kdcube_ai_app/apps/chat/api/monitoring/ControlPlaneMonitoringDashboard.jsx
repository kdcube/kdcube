"use strict";
// Control Plane Monitoring Dashboard (TypeScript)
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
Object.defineProperty(exports, "__esModule", { value: true });
var react_1 = require("react");
var client_1 = require("react-dom/client");
// =============================================================================
// Settings Manager (same pattern as other widgets)
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
        var identity = 'CONTROL_PLANE_MONITORING';
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
// =============================================================================
// API Client
// =============================================================================
var MonitoringAPI = /** @class */ (function () {
    function MonitoringAPI(basePath) {
        if (basePath === void 0) { basePath = ''; }
        this.basePath = basePath;
    }
    MonitoringAPI.prototype.url = function (path) {
        return "".concat(settings.getBaseUrl()).concat(this.basePath).concat(path);
    };
    //
    // private url(path: string): string {
    //     return `${this.baseUrl}${path}`;
    // }
    MonitoringAPI.prototype.getSystemStatus = function () {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url('/monitoring/system'), {
                            method: 'GET',
                            headers: makeAuthHeaders(),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Failed to load system status (".concat(res.status, ")"));
                        return [2 /*return*/, res.json()];
                }
            });
        });
    };
    MonitoringAPI.prototype.getCircuitBreakers = function () {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url('/admin/circuit-breakers'), {
                            method: 'GET',
                            headers: makeAuthHeaders(),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Failed to load circuit breakers (".concat(res.status, ")"));
                        return [2 /*return*/, res.json()];
                }
            });
        });
    };
    MonitoringAPI.prototype.resetCircuitBreaker = function (name) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url("/admin/circuit-breakers/".concat(name, "/reset")), {
                            method: 'POST',
                            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Failed to reset circuit breaker (".concat(res.status, ")"));
                        return [2 /*return*/];
                }
            });
        });
    };
    MonitoringAPI.prototype.validateGatewayConfig = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url('/admin/gateway/validate-config'), {
                            method: 'POST',
                            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify(payload),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Validation failed (".concat(res.status, ")"));
                        return [2 /*return*/, res.json()];
                }
            });
        });
    };
    MonitoringAPI.prototype.updateGatewayConfig = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url('/admin/gateway/update-config'), {
                            method: 'POST',
                            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify(payload),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Update failed (".concat(res.status, ")"));
                        return [2 /*return*/, res.json()];
                }
            });
        });
    };
    MonitoringAPI.prototype.resetGatewayConfig = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var res;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, fetch(this.url('/admin/gateway/reset-config'), {
                            method: 'POST',
                            headers: makeAuthHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify(payload),
                        })];
                    case 1:
                        res = _a.sent();
                        if (!res.ok)
                            throw new Error("Reset failed (".concat(res.status, ")"));
                        return [2 /*return*/, res.json()];
                }
            });
        });
    };
    return MonitoringAPI;
}());
// =============================================================================
// UI Components (simple, neutral palette)
// =============================================================================
var Card = function (_a) {
    var children = _a.children, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={"bg-white rounded-2xl shadow-sm border border-gray-200/70 ".concat(className)}>
        {children}
    </div>);
};
var CapacityPanel = function (_a) {
    var _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t, _u, _v, _w, _x, _y, _z, _0;
    var capacity = _a.capacity;
    if (!capacity)
        return null;
    var metrics = capacity.capacity_metrics || {};
    var scaling = capacity.instance_scaling || {};
    var thresholds = capacity.threshold_breakdown || {};
    var warnings = capacity.capacity_warnings || [];
    var hasActual = metrics.actual_runtime && metrics.health_metrics;
    var health = metrics.health_metrics || {};
    return (<Card>
            <CardHeader title="Capacity Transparency" subtitle="Actual runtime vs configured capacity."/>
            <CardBody className="space-y-4">
                {warnings.length > 0 && (<div className="p-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-sm">
                        {warnings.map(function (w, i) { return (<div key={i}>• {w}</div>); })}
                    </div>)}

                {hasActual && (<div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Configured</div>
                            <div className="text-sm font-semibold">{(_c = (_b = health.processes_vs_configured) === null || _b === void 0 ? void 0 : _b.configured) !== null && _c !== void 0 ? _c : '—'}</div>
                            <div className="text-xs text-gray-500">processes</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Actual</div>
                            <div className="text-sm font-semibold">{(_e = (_d = health.processes_vs_configured) === null || _d === void 0 ? void 0 : _d.actual) !== null && _e !== void 0 ? _e : '—'}</div>
                            <div className="text-xs text-gray-500">running</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Healthy</div>
                            <div className="text-sm font-semibold">{(_g = (_f = health.processes_vs_configured) === null || _f === void 0 ? void 0 : _f.healthy) !== null && _g !== void 0 ? _g : '—'}</div>
                            <div className="text-xs text-gray-500">{Math.round(((_h = health.process_health_ratio) !== null && _h !== void 0 ? _h : 0) * 100)}% health</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Process Deficit</div>
                            <div className="text-sm font-semibold">{(_k = (_j = health.processes_vs_configured) === null || _j === void 0 ? void 0 : _j.process_deficit) !== null && _k !== void 0 ? _k : 0}</div>
                            <div className="text-xs text-gray-500">missing</div>
                        </div>
                    </div>)}

                {metrics.actual_runtime && metrics.configuration && (<div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Per Process</div>
                            <div className="text-sm font-semibold">{(_l = metrics.configuration.configured_concurrent_per_process) !== null && _l !== void 0 ? _l : '—'}</div>
                            <div className="text-xs text-gray-500">{(_m = metrics.configuration.configured_avg_processing_time_seconds) !== null && _m !== void 0 ? _m : '—'}s avg</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Actual Concurrent</div>
                            <div className="text-sm font-semibold">{(_o = metrics.actual_runtime.actual_concurrent_per_instance) !== null && _o !== void 0 ? _o : '—'}</div>
                            <div className="text-xs text-gray-500">per instance</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Effective</div>
                            <div className="text-sm font-semibold">{(_p = metrics.actual_runtime.actual_effective_concurrent_per_instance) !== null && _p !== void 0 ? _p : '—'}</div>
                            <div className="text-xs text-gray-500">after buffer</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Total Capacity</div>
                            <div className="text-sm font-semibold">{(_q = metrics.actual_runtime.actual_total_capacity_per_instance) !== null && _q !== void 0 ? _q : '—'}</div>
                            <div className="text-xs text-gray-500">per instance</div>
                        </div>
                    </div>)}

                {scaling && (<div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Instances</div>
                            <div className="text-sm font-semibold">{(_r = scaling.detected_instances) !== null && _r !== void 0 ? _r : '—'}</div>
                            <div className="text-xs text-gray-500">detected</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">System Concurrent</div>
                            <div className="text-sm font-semibold">{(_s = scaling.total_concurrent_capacity) !== null && _s !== void 0 ? _s : '—'}</div>
                            <div className="text-xs text-gray-500">total</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">System Total</div>
                            <div className="text-sm font-semibold">{(_t = scaling.total_system_capacity) !== null && _t !== void 0 ? _t : '—'}</div>
                            <div className="text-xs text-gray-500">capacity</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Health Ratio</div>
                            <div className="text-sm font-semibold">{Math.round(((_u = scaling.process_health_ratio) !== null && _u !== void 0 ? _u : 0) * 100)}%</div>
                            <div className="text-xs text-gray-500">system</div>
                        </div>
                    </div>)}

                {thresholds && (<div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Anonymous Blocks At</div>
                            <div className="text-sm font-semibold">{(_v = thresholds.anonymous_blocks_at) !== null && _v !== void 0 ? _v : '—'}</div>
                            <div className="text-xs text-gray-500">{(_w = thresholds.anonymous_percentage) !== null && _w !== void 0 ? _w : '—'}%</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Registered Blocks At</div>
                            <div className="text-sm font-semibold">{(_x = thresholds.registered_blocks_at) !== null && _x !== void 0 ? _x : '—'}</div>
                            <div className="text-xs text-gray-500">{(_y = thresholds.registered_percentage) !== null && _y !== void 0 ? _y : '—'}%</div>
                        </div>
                        <div className="p-3 rounded-xl bg-gray-100">
                            <div className="text-xs text-gray-600">Hard Limit At</div>
                            <div className="text-sm font-semibold">{(_z = thresholds.hard_limit_at) !== null && _z !== void 0 ? _z : '—'}</div>
                            <div className="text-xs text-gray-500">{(_0 = thresholds.hard_limit_percentage) !== null && _0 !== void 0 ? _0 : '—'}%</div>
                        </div>
                    </div>)}
            </CardBody>
        </Card>);
};
var CardHeader = function (_a) {
    var title = _a.title, subtitle = _a.subtitle, action = _a.action;
    return (<div className="px-4 py-3 border-b border-gray-200/70">
        <div className="flex items-start justify-between gap-4">
            <div>
                <h2 className="text-base font-semibold text-gray-900">{title}</h2>
                {subtitle && <p className="mt-1 text-xs text-gray-600 leading-relaxed">{subtitle}</p>}
            </div>
            {action && <div className="pt-1">{action}</div>}
        </div>
    </div>);
};
var CardBody = function (_a) {
    var children = _a.children, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={"px-4 py-3 ".concat(className)}>
        {children}
    </div>);
};
var Button = function (_a) {
    var children = _a.children, onClick = _a.onClick, _b = _a.type, type = _b === void 0 ? 'button' : _b, _c = _a.variant, variant = _c === void 0 ? 'primary' : _c, _d = _a.disabled, disabled = _d === void 0 ? false : _d, _e = _a.className, className = _e === void 0 ? '' : _e;
    var variants = {
        primary: 'bg-gray-900 hover:bg-gray-800 text-white',
        secondary: 'bg-white hover:bg-gray-50 text-gray-900 border border-gray-200/80',
        danger: 'bg-rose-600 hover:bg-rose-700 text-white',
    };
    return (<button type={type} onClick={onClick} disabled={disabled} className={"px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ".concat(variants[variant], " ").concat(className)}>
            {children}
        </button>);
};
var Input = function (_a) {
    var label = _a.label, value = _a.value, onChange = _a.onChange, placeholder = _a.placeholder, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={className}>
        {label && <label className="block text-xs font-medium text-gray-800 mb-1.5">{label}</label>}
        <input type="text" value={value} onChange={onChange} placeholder={placeholder} className="w-full px-3 py-1.5 border border-gray-200/80 rounded-lg bg-white text-xs focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors placeholder:text-gray-400"/>
    </div>);
};
var TextArea = function (_a) {
    var label = _a.label, value = _a.value, onChange = _a.onChange, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={className}>
        {label && <label className="block text-xs font-medium text-gray-800 mb-1.5">{label}</label>}
        <textarea value={value} onChange={onChange} rows={10} className="w-full px-3 py-2 border border-gray-200/80 rounded-lg bg-white font-mono text-xs leading-relaxed focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"/>
    </div>);
};
var Pill = function (_a) {
    var _b = _a.tone, tone = _b === void 0 ? 'neutral' : _b, children = _a.children;
    var tones = {
        neutral: 'bg-gray-100 text-gray-700',
        success: 'bg-emerald-100 text-emerald-700',
        warning: 'bg-amber-100 text-amber-700',
        danger: 'bg-rose-100 text-rose-700',
    };
    return <span className={"px-2 py-0.5 rounded-full text-[10px] font-semibold ".concat(tones[tone])}>{children}</span>;
};
// =============================================================================
// App
// =============================================================================
var MonitoringDashboard = function () {
    var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t;
    var api = (0, react_1.useMemo)(function () { return new MonitoringAPI(); }, []);
    var _u = (0, react_1.useState)(null), system = _u[0], setSystem = _u[1];
    var _v = (0, react_1.useState)({}), circuitBreakers = _v[0], setCircuitBreakers = _v[1];
    var _w = (0, react_1.useState)(null), circuitSummary = _w[0], setCircuitSummary = _w[1];
    var _x = (0, react_1.useState)(false), loading = _x[0], setLoading = _x[1];
    var _y = (0, react_1.useState)(null), error = _y[0], setError = _y[1];
    var _z = (0, react_1.useState)(true), autoRefresh = _z[0], setAutoRefresh = _z[1];
    var _0 = (0, react_1.useState)(null), lastUpdate = _0[0], setLastUpdate = _0[1];
    var _1 = (0, react_1.useState)(settings.getDefaultTenant()), tenant = _1[0], setTenant = _1[1];
    var _2 = (0, react_1.useState)(settings.getDefaultProject()), project = _2[0], setProject = _2[1];
    var _3 = (0, react_1.useState)(false), dryRun = _3[0], setDryRun = _3[1];
    var _4 = (0, react_1.useState)(''), configJson = _4[0], setConfigJson = _4[1];
    var _5 = (0, react_1.useState)(null), validationResult = _5[0], setValidationResult = _5[1];
    var _6 = (0, react_1.useState)(null), actionMessage = _6[0], setActionMessage = _6[1];
    var refreshAll = (0, react_1.useCallback)(function () { return __awaiter(void 0, void 0, void 0, function () {
        var _a, sys, cb, e_1;
        return __generator(this, function (_b) {
            switch (_b.label) {
                case 0:
                    setLoading(true);
                    setError(null);
                    _b.label = 1;
                case 1:
                    _b.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, Promise.all([
                            api.getSystemStatus(),
                            api.getCircuitBreakers(),
                        ])];
                case 2:
                    _a = _b.sent(), sys = _a[0], cb = _a[1];
                    setSystem(sys);
                    setCircuitBreakers(cb.circuits || {});
                    setCircuitSummary(cb.summary || null);
                    setLastUpdate(new Date().toLocaleTimeString());
                    return [3 /*break*/, 5];
                case 3:
                    e_1 = _b.sent();
                    setError((e_1 === null || e_1 === void 0 ? void 0 : e_1.message) || 'Failed to load monitoring data');
                    return [3 /*break*/, 5];
                case 4:
                    setLoading(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); }, [api]);
    (0, react_1.useEffect)(function () {
        var mounted = true;
        settings.setupParentListener().then(function () {
            if (mounted)
                refreshAll();
        });
        return function () { mounted = false; };
    }, [refreshAll]);
    (0, react_1.useEffect)(function () {
        if (!autoRefresh)
            return;
        var t = setInterval(function () { return refreshAll(); }, 5000);
        return function () { return clearInterval(t); };
    }, [autoRefresh, refreshAll]);
    (0, react_1.useEffect)(function () {
        var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s;
        if (!(system === null || system === void 0 ? void 0 : system.gateway_configuration))
            return;
        var cfg = system.gateway_configuration;
        var capacityCfg = ((_b = (_a = system.capacity_transparency) === null || _a === void 0 ? void 0 : _a.capacity_metrics) === null || _b === void 0 ? void 0 : _b.configuration) || {};
        var payload = {
            tenant: tenant,
            project: project,
            guarded_rest_patterns: cfg.guarded_rest_patterns || [],
            service_capacity: {
                concurrent_per_process: (_c = capacityCfg.configured_concurrent_per_process) !== null && _c !== void 0 ? _c : 5,
                processes_per_instance: (_d = capacityCfg.configured_processes_per_instance) !== null && _d !== void 0 ? _d : 1,
                avg_processing_time_seconds: (_e = capacityCfg.configured_avg_processing_time_seconds) !== null && _e !== void 0 ? _e : ((_g = (_f = cfg.service_capacity) === null || _f === void 0 ? void 0 : _f.avg_processing_time_seconds) !== null && _g !== void 0 ? _g : 25),
            },
            backpressure: {
                capacity_buffer: (_j = (_h = cfg.backpressure_settings) === null || _h === void 0 ? void 0 : _h.capacity_buffer) !== null && _j !== void 0 ? _j : 0.2,
                queue_depth_multiplier: (_l = (_k = cfg.backpressure_settings) === null || _k === void 0 ? void 0 : _k.queue_depth_multiplier) !== null && _l !== void 0 ? _l : 2.0,
                anonymous_pressure_threshold: (_o = (_m = cfg.backpressure_settings) === null || _m === void 0 ? void 0 : _m.anonymous_pressure_threshold) !== null && _o !== void 0 ? _o : 0.6,
                registered_pressure_threshold: (_q = (_p = cfg.backpressure_settings) === null || _p === void 0 ? void 0 : _p.registered_pressure_threshold) !== null && _q !== void 0 ? _q : 0.8,
                hard_limit_threshold: (_s = (_r = cfg.backpressure_settings) === null || _r === void 0 ? void 0 : _r.hard_limit_threshold) !== null && _s !== void 0 ? _s : 0.95,
            },
            rate_limits: cfg.rate_limits || {},
        };
        setConfigJson(JSON.stringify(payload, null, 2));
    }, [system, tenant, project]);
    var queue = system === null || system === void 0 ? void 0 : system.queue_stats;
    var capacityCtx = ((_a = system === null || system === void 0 ? void 0 : system.queue_stats) === null || _a === void 0 ? void 0 : _a.capacity_context) || {};
    var throttling = system === null || system === void 0 ? void 0 : system.throttling_stats;
    var events = (system === null || system === void 0 ? void 0 : system.recent_throttling_events) || [];
    var gateway = system === null || system === void 0 ? void 0 : system.gateway_configuration;
    var throttlingByPeriod = (system === null || system === void 0 ? void 0 : system.throttling_by_period) || {};
    var handleValidate = function () { return __awaiter(void 0, void 0, void 0, function () {
        var payload, res, e_2;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    _a.trys.push([0, 2, , 3]);
                    payload = JSON.parse(configJson);
                    return [4 /*yield*/, api.validateGatewayConfig(payload)];
                case 1:
                    res = _a.sent();
                    setValidationResult(res);
                    setActionMessage('Validation completed');
                    return [3 /*break*/, 3];
                case 2:
                    e_2 = _a.sent();
                    setActionMessage((e_2 === null || e_2 === void 0 ? void 0 : e_2.message) || 'Validation failed');
                    return [3 /*break*/, 3];
                case 3: return [2 /*return*/];
            }
        });
    }); };
    var handleUpdate = function () { return __awaiter(void 0, void 0, void 0, function () {
        var payload, e_3;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    _a.trys.push([0, 3, , 4]);
                    payload = JSON.parse(configJson);
                    return [4 /*yield*/, api.updateGatewayConfig(payload)];
                case 1:
                    _a.sent();
                    setActionMessage('Config updated');
                    return [4 /*yield*/, refreshAll()];
                case 2:
                    _a.sent();
                    return [3 /*break*/, 4];
                case 3:
                    e_3 = _a.sent();
                    setActionMessage((e_3 === null || e_3 === void 0 ? void 0 : e_3.message) || 'Update failed');
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    }); };
    var handleReset = function () { return __awaiter(void 0, void 0, void 0, function () {
        var payload, e_4;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    _a.trys.push([0, 3, , 4]);
                    payload = { tenant: tenant, project: project, dry_run: dryRun };
                    return [4 /*yield*/, api.resetGatewayConfig(payload)];
                case 1:
                    _a.sent();
                    setActionMessage(dryRun ? 'Dry run completed' : 'Config reset to env');
                    return [4 /*yield*/, refreshAll()];
                case 2:
                    _a.sent();
                    return [3 /*break*/, 4];
                case 3:
                    e_4 = _a.sent();
                    setActionMessage((e_4 === null || e_4 === void 0 ? void 0 : e_4.message) || 'Reset failed');
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    }); };
    var resetCircuit = function (name) { return __awaiter(void 0, void 0, void 0, function () {
        var e_5;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    _a.trys.push([0, 3, , 4]);
                    return [4 /*yield*/, api.resetCircuitBreaker(name)];
                case 1:
                    _a.sent();
                    return [4 /*yield*/, refreshAll()];
                case 2:
                    _a.sent();
                    return [3 /*break*/, 4];
                case 3:
                    e_5 = _a.sent();
                    setActionMessage((e_5 === null || e_5 === void 0 ? void 0 : e_5.message) || 'Failed to reset circuit breaker');
                    return [3 /*break*/, 4];
                case 4: return [2 /*return*/];
            }
        });
    }); };
    return (<div className="min-h-screen bg-gray-50 text-gray-900">
            <div className="max-w-6xl mx-auto px-4 py-4 space-y-4">
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <h1 className="text-lg font-semibold">Gateway Monitoring</h1>
                        <p className="text-xs text-gray-600">System health, queues, throttling, and config management.</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <label className="text-[11px] text-gray-600 flex items-center gap-2">
                            <input type="checkbox" checked={autoRefresh} onChange={function (e) { return setAutoRefresh(e.target.checked); }}/>
                            Auto refresh
                        </label>
                        <Button variant="secondary" onClick={refreshAll} disabled={loading}>
                            {loading ? 'Refreshing…' : 'Refresh'}
                        </Button>
                    </div>
                </div>

                {error && (<Card>
                        <CardBody>
                            <div className="text-xs text-rose-700">{error}</div>
                        </CardBody>
                    </Card>)}

                <Card>
                    <CardHeader title="System Summary" subtitle={"Last update: ".concat(lastUpdate || '—')} action={gateway ? <Pill tone="success">{gateway.current_profile}</Pill> : null}/>
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Instance</div>
                                <div className="text-sm font-semibold">{(gateway === null || gateway === void 0 ? void 0 : gateway.instance_id) || '—'}</div>
                                <div className="text-xs text-gray-500">{(gateway === null || gateway === void 0 ? void 0 : gateway.tenant_id) || '—'}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Total Queue</div>
                                <div className="text-sm font-semibold">{(_b = queue === null || queue === void 0 ? void 0 : queue.total) !== null && _b !== void 0 ? _b : 0}</div>
                                <div className="text-xs text-gray-500">{Math.round((capacityCtx.pressure_ratio || 0) * 100)}% pressure</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Instances</div>
                                <div className="text-sm font-semibold">{(_c = capacityCtx.instance_count) !== null && _c !== void 0 ? _c : 0}</div>
                                <div className="text-xs text-gray-500">Weighted cap {(_d = capacityCtx.weighted_max_capacity) !== null && _d !== void 0 ? _d : 0}</div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Throttled (1h)</div>
                                <div className="text-sm font-semibold">{(_e = throttling === null || throttling === void 0 ? void 0 : throttling.total_throttled) !== null && _e !== void 0 ? _e : 0}</div>
                                <div className="text-xs text-gray-500">{((_f = throttling === null || throttling === void 0 ? void 0 : throttling.throttle_rate) !== null && _f !== void 0 ? _f : 0).toFixed(1)}%</div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Traffic (Requests)" subtitle="Totals and average per minute by period."/>
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {["1h", "3h", "24h"].map(function (key) {
            var _a;
            var period = throttlingByPeriod[key] || {};
            var total = (_a = period.total_requests) !== null && _a !== void 0 ? _a : 0;
            var hours = parseInt(key.replace("h", ""), 10) || 1;
            var perHour = hours ? total / hours : 0;
            var perMin = perHour / 60;
            return (<div key={key} className="p-4 rounded-xl bg-gray-100">
                                        <div className="text-xs text-gray-600">{key} total</div>
                                        <div className="text-sm font-semibold">{Math.round(total)}</div>
                                        <div className="text-xs text-gray-500">
                                            ~{Math.round(perMin)} / min · ~{Math.round(perHour)} / hour
                                        </div>
                                    </div>);
        })}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Queues" subtitle="Current queue sizes and admission state."/>
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Anonymous</div>
                                <div className="text-sm font-semibold">{(_g = queue === null || queue === void 0 ? void 0 : queue.anonymous) !== null && _g !== void 0 ? _g : 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_anonymous ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Registered</div>
                                <div className="text-sm font-semibold">{(_h = queue === null || queue === void 0 ? void 0 : queue.registered) !== null && _h !== void 0 ? _h : 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_registered ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Privileged</div>
                                <div className="text-sm font-semibold">{(_j = queue === null || queue === void 0 ? void 0 : queue.privileged) !== null && _j !== void 0 ? _j : 0}</div>
                                <div className="text-xs text-gray-500">
                                    {capacityCtx.accepting_privileged ? 'accepting' : 'blocked'}
                                </div>
                            </div>
                            <div className="p-4 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Hard Limit</div>
                                <div className="text-sm font-semibold">{(_l = (_k = capacityCtx.thresholds) === null || _k === void 0 ? void 0 : _k.hard_limit_threshold) !== null && _l !== void 0 ? _l : 0}</div>
                                <div className="text-xs text-gray-500">items</div>
                            </div>
                        </div>
                    </CardBody>
                </Card>

                <CapacityPanel capacity={system === null || system === void 0 ? void 0 : system.capacity_transparency}/>

                <Card>
                    <CardHeader title="Circuit Breakers" subtitle="Live circuit states and resets."/>
                    <CardBody>
                        <div className="flex items-center gap-3 mb-4">
                            <Pill tone={(circuitSummary === null || circuitSummary === void 0 ? void 0 : circuitSummary.open_circuits) ? 'danger' : 'success'}>
                                Open: {(_m = circuitSummary === null || circuitSummary === void 0 ? void 0 : circuitSummary.open_circuits) !== null && _m !== void 0 ? _m : 0}
                            </Pill>
                            <Pill tone="neutral">Half-open: {(_o = circuitSummary === null || circuitSummary === void 0 ? void 0 : circuitSummary.half_open_circuits) !== null && _o !== void 0 ? _o : 0}</Pill>
                            <Pill tone="neutral">Closed: {(_p = circuitSummary === null || circuitSummary === void 0 ? void 0 : circuitSummary.closed_circuits) !== null && _p !== void 0 ? _p : 0}</Pill>
                        </div>
                        <div className="space-y-3">
                            {Object.entries(circuitBreakers).map(function (_a) {
            var name = _a[0], cb = _a[1];
            return (<div key={name} className="flex items-center justify-between p-3 rounded-xl bg-gray-100">
                                    <div className="text-sm">
                                        <div className="font-semibold">{name}</div>
                                        <div className="text-xs text-gray-600">
                                            state: {cb.state} • failures: {cb.current_window_failures}/{cb.failure_count}
                                        </div>
                                    </div>
                                    <Button variant="secondary" onClick={function () { return resetCircuit(name); }}>
                                        Reset
                                    </Button>
                                </div>);
        })}
                        </div>
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Gateway Configuration" subtitle="View, validate, update, or reset config."/>
                    <CardBody className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <Input label="Tenant" value={tenant} onChange={function (e) { return setTenant(e.target.value); }}/>
                            <Input label="Project" value={project} onChange={function (e) { return setProject(e.target.value); }}/>
                            <div className="flex items-end gap-3">
                                <label className="text-xs text-gray-600 flex items-center gap-2">
                                    <input type="checkbox" checked={dryRun} onChange={function (e) { return setDryRun(e.target.checked); }}/>
                                    Dry run reset
                                </label>
                            </div>
                        </div>

                        <TextArea label="Update Payload (JSON)" value={configJson} onChange={function (e) { return setConfigJson(e.target.value); }}/>

                        <div className="flex flex-wrap gap-3">
                            <Button variant="secondary" onClick={handleValidate}>Validate</Button>
                            <Button onClick={handleUpdate}>Update</Button>
                            <Button variant="danger" onClick={handleReset}>Reset to Env</Button>
                            {actionMessage && <span className="text-sm text-gray-600">{actionMessage}</span>}
                        </div>

                        {validationResult && (<div className="mt-4 p-3 rounded-xl bg-gray-100 text-xs font-mono whitespace-pre-wrap">
                                {JSON.stringify(validationResult, null, 2)}
                            </div>)}
                    </CardBody>
                </Card>

                <Card>
                    <CardHeader title="Throttling (Recent)" subtitle="Last hour summary and recent events."/>
                    <CardBody>
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Total</div>
                                <div className="text-sm font-semibold">{(_q = throttling === null || throttling === void 0 ? void 0 : throttling.total_requests) !== null && _q !== void 0 ? _q : 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">Throttled</div>
                                <div className="text-sm font-semibold">{(_r = throttling === null || throttling === void 0 ? void 0 : throttling.total_throttled) !== null && _r !== void 0 ? _r : 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">429</div>
                                <div className="text-sm font-semibold">{(_s = throttling === null || throttling === void 0 ? void 0 : throttling.rate_limit_429) !== null && _s !== void 0 ? _s : 0}</div>
                            </div>
                            <div className="p-3 rounded-xl bg-gray-100">
                                <div className="text-xs text-gray-600">503</div>
                                <div className="text-sm font-semibold">{(_t = throttling === null || throttling === void 0 ? void 0 : throttling.backpressure_503) !== null && _t !== void 0 ? _t : 0}</div>
                            </div>
                        </div>

                        <div className="space-y-2">
                            {events.slice(0, 10).map(function (e, idx) { return (<div key={e.event_id || idx} className="text-xs flex items-center justify-between bg-white border border-gray-200/70 rounded-xl px-3 py-2">
                                    <div className="text-gray-700">{e.reason}</div>
                                    <div className="text-gray-500">{e.user_type}</div>
                                    <div className="text-gray-500">{e.http_status}</div>
                                </div>); })}
                            {events.length === 0 && <div className="text-sm text-gray-500">No recent events.</div>}
                        </div>
                    </CardBody>
                </Card>
            </div>
        </div>);
};
// Render
var rootElement = document.getElementById('root');
if (rootElement) {
    var root = client_1.default.createRoot(rootElement);
    root.render(<MonitoringDashboard />);
}
