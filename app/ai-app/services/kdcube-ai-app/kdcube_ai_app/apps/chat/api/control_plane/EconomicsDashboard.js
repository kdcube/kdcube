"use strict";
// COMPLETE PROFESSIONAL VERSION - Control Plane Admin React App (TypeScript)
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
                console.warn('[SettingsManager] Invalid baseUrl detected, using fallback');
                return 'http://localhost:8010';
            }
            return this.settings.baseUrl;
        }
        catch (e) {
            console.warn('[SettingsManager] Invalid baseUrl, using fallback:', this.settings.baseUrl);
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
    SettingsManager.prototype.getDefaultAppBundleId = function () {
        return this.settings.defaultAppBundleId === this.PLACEHOLDER_BUNDLE_ID
            ? 'kdcube.codegen.orchestrator'
            : this.settings.defaultAppBundleId;
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
        console.log('[SettingsManager] Setting up parent listener');
        var identity = "CONTROL_PLANE_ADMIN";
        window.addEventListener('message', function (event) {
            if (event.data.type === 'CONN_RESPONSE' || event.data.type === 'CONFIG_RESPONSE') {
                var requestedIdentity = event.data.identity;
                if (requestedIdentity !== identity) {
                    console.warn("[SettingsManager] Ignoring response for identity ".concat(requestedIdentity));
                    return;
                }
                console.log('[SettingsManager] Received config from parent', event.data.config);
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
                        console.log('[SettingsManager] Settings updated from parent');
                        if (_this.configReceivedCallback) {
                            _this.configReceivedCallback();
                        }
                    }
                }
            }
        });
        if (this.hasPlaceholderSettings()) {
            console.log('[SettingsManager] Requesting config from parent');
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
                    console.log('[SettingsManager] Config request timeout');
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
        else {
            console.log('[SettingsManager] Using existing settings');
            return Promise.resolve(!this.hasPlaceholderSettings());
        }
    };
    return SettingsManager;
}());
var settings = new SettingsManager();
// =============================================================================
// Auth Header Helper
// =============================================================================
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
// Control Plane API Client
// =============================================================================
var ControlPlaneAPI = /** @class */ (function () {
    function ControlPlaneAPI(basePath) {
        if (basePath === void 0) { basePath = '/api/admin/control-plane'; }
        this.basePath = basePath;
    }
    ControlPlaneAPI.prototype.getFullUrl = function (path) {
        var baseUrl = settings.getBaseUrl();
        return "".concat(baseUrl).concat(this.basePath).concat(path);
    };
    ControlPlaneAPI.prototype.fetchWithAuth = function (url_1) {
        return __awaiter(this, arguments, void 0, function (url, options) {
            var headers, response, errorText;
            if (options === void 0) { options = {}; }
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        headers = makeAuthHeaders(options.headers);
                        return [4 /*yield*/, fetch(url, __assign(__assign({}, options), { headers: headers }))];
                    case 1:
                        response = _a.sent();
                        if (!!response.ok) return [3 /*break*/, 3];
                        return [4 /*yield*/, response.text().catch(function () { return response.statusText; })];
                    case 2:
                        errorText = _a.sent();
                        throw new Error("API request failed: ".concat(response.status, " - ").concat(errorText));
                    case 3: return [2 /*return*/, response];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.grantTrial = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/tier-balance/grant-trial'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_id: payload.userId,
                                days: payload.days,
                                requests_per_day: payload.requestsPerDay,
                                tokens_per_hour: payload.tokensPerHour,
                                tokens_per_day: payload.tokensPerDay,
                                tokens_per_month: payload.tokensPerMonth,
                                usd_per_hour: payload.usdPerHour,
                                usd_per_day: payload.usdPerDay,
                                usd_per_month: payload.usdPerMonth,
                                notes: payload.notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.updateTierBudget = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/tier-balance/update'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_id: payload.userId,
                                requests_per_day: payload.requestsPerDay,
                                requests_per_month: payload.requestsPerMonth,
                                tokens_per_hour: payload.tokensPerHour,
                                tokens_per_day: payload.tokensPerDay,
                                tokens_per_month: payload.tokensPerMonth,
                                usd_per_hour: payload.usdPerHour,
                                usd_per_day: payload.usdPerDay,
                                usd_per_month: payload.usdPerMonth,
                                max_concurrent: payload.maxConcurrent,
                                expires_in_days: payload.expiresInDays,
                                notes: payload.notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.getTierBalance = function (userId_1) {
        return __awaiter(this, arguments, void 0, function (userId, includeExpired) {
            var queryParams, response;
            if (includeExpired === void 0) { includeExpired = false; }
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        queryParams = new URLSearchParams({
                            include_expired: includeExpired.toString()
                        });
                        return [4 /*yield*/, this.fetchWithAuth("".concat(this.getFullUrl("/tier-balance/user/".concat(userId)), "?").concat(queryParams))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.deactivateTierBalance = function (userId) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl("/tier-balance/user/".concat(userId)), { method: 'DELETE' })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.addLifetimeCredits = function (userId, usdAmount, notes) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/tier-balance/add-lifetime-credits'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_id: userId,
                                usd_amount: usdAmount,
                                ref_provider: 'anthropic',
                                ref_model: 'claude-sonnet-4-5-20250929',
                                notes: notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.getLifetimeBalance = function (userId) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl("/tier-balance/lifetime-balance/".concat(userId)))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.listQuotaPolicies = function () {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/policies/quota'))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.setQuotaPolicy = function (policy) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/policies/quota'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_type: policy.userType,
                                max_concurrent: policy.maxConcurrent,
                                requests_per_day: policy.requestsPerDay,
                                requests_per_month: policy.requestsPerMonth,
                                total_requests: policy.totalRequests,
                                tokens_per_hour: policy.tokensPerHour,
                                tokens_per_day: policy.tokensPerDay,
                                tokens_per_month: policy.tokensPerMonth,
                                usd_per_hour: policy.usdPerHour,
                                usd_per_day: policy.usdPerDay,
                                usd_per_month: policy.usdPerMonth,
                                notes: policy.notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.listBudgetPolicies = function () {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/policies/budget'))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.setBudgetPolicy = function (policy) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/policies/budget'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                provider: policy.provider,
                                usd_per_hour: policy.usdPerHour,
                                usd_per_day: policy.usdPerDay,
                                usd_per_month: policy.usdPerMonth,
                                notes: policy.notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    // async getUserQuotaBreakdown(userId: string, userType: string): Promise<{ status: string; } & QuotaBreakdown> {
    //     const queryParams = new URLSearchParams({
    //         user_type: userType
    //     });
    //     const response = await this.fetchWithAuth(
    //         `${this.getFullUrl(`/users/${userId}/quota-breakdown`)}?${queryParams}`
    //     );
    //     return response.json();
    // }
    ControlPlaneAPI.prototype.getUserBudgetBreakdown = function (userId, userType) {
        return __awaiter(this, void 0, void 0, function () {
            var queryParams, response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        queryParams = new URLSearchParams({
                            user_type: userType,
                            include_expired_override: 'true',
                            reservations_limit: '50',
                        });
                        return [4 /*yield*/, this.fetchWithAuth("".concat(this.getFullUrl("/users/".concat(userId, "/budget-breakdown")), "?").concat(queryParams))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.getAppBudgetBalance = function () {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/app-budget/balance'))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.getEconomicsReference = function () {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/economics/reference'))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.topupAppBudget = function (usdAmount, notes) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/app-budget/topup'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                usd_amount: usdAmount,
                                notes: notes
                            })
                        })];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.healthCheck = function () {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/health'))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.createSubscription = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            var _a, _b, _c;
            return __generator(this, function (_d) {
                switch (_d.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/subscriptions/create'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_id: payload.userId,
                                tier: payload.tier,
                                provider: payload.provider,
                                stripe_price_id: (_a = payload.stripePriceId) !== null && _a !== void 0 ? _a : null,
                                stripe_customer_id: (_b = payload.stripeCustomerId) !== null && _b !== void 0 ? _b : null,
                                monthly_price_cents_hint: (_c = payload.monthlyPriceCentsHint) !== null && _c !== void 0 ? _c : null,
                            })
                        })];
                    case 1:
                        response = _d.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.getSubscription = function (userId) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl("/subscriptions/user/".concat(userId)))];
                    case 1:
                        response = _a.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.listSubscriptions = function (params) {
        return __awaiter(this, void 0, void 0, function () {
            var qp, response;
            var _a, _b;
            return __generator(this, function (_c) {
                switch (_c.label) {
                    case 0:
                        qp = new URLSearchParams();
                        if (params === null || params === void 0 ? void 0 : params.provider)
                            qp.set('provider', params.provider);
                        if (params === null || params === void 0 ? void 0 : params.userId)
                            qp.set('user_id', params.userId);
                        qp.set('limit', String((_a = params === null || params === void 0 ? void 0 : params.limit) !== null && _a !== void 0 ? _a : 50));
                        qp.set('offset', String((_b = params === null || params === void 0 ? void 0 : params.offset) !== null && _b !== void 0 ? _b : 0));
                        return [4 /*yield*/, this.fetchWithAuth("".concat(this.getFullUrl('/subscriptions/list'), "?").concat(qp.toString()))];
                    case 1:
                        response = _c.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    ControlPlaneAPI.prototype.renewInternalSubscriptionOnce = function (payload) {
        return __awaiter(this, void 0, void 0, function () {
            var response;
            var _a, _b;
            return __generator(this, function (_c) {
                switch (_c.label) {
                    case 0: return [4 /*yield*/, this.fetchWithAuth(this.getFullUrl('/subscriptions/internal/renew-once'), {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                user_id: payload.userId,
                                charge_at: (_a = payload.chargeAt) !== null && _a !== void 0 ? _a : null,
                                idempotency_key: (_b = payload.idempotencyKey) !== null && _b !== void 0 ? _b : null,
                            }),
                        })];
                    case 1:
                        response = _c.sent();
                        return [2 /*return*/, response.json()];
                }
            });
        });
    };
    return ControlPlaneAPI;
}());
// =============================================================================
// UI Components (gentle styling)
// =============================================================================
var Card = function (_a) {
    var children = _a.children, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={"bg-white rounded-2xl shadow-sm border border-gray-200/70 ".concat(className)}>
        {children}
    </div>);
};
var CardHeader = function (_a) {
    var title = _a.title, subtitle = _a.subtitle, action = _a.action;
    return (<div className="px-6 py-5 border-b border-gray-200/70">
        <div className="flex items-start justify-between gap-4">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">{title}</h2>
                {subtitle && <p className="mt-1 text-sm text-gray-600 leading-relaxed">{subtitle}</p>}
            </div>
            {action && <div className="pt-1">{action}</div>}
        </div>
    </div>);
};
var CardBody = function (_a) {
    var children = _a.children, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={"px-6 py-5 ".concat(className)}>
        {children}
    </div>);
};
var Callout = function (_a) {
    var _b = _a.tone, tone = _b === void 0 ? 'neutral' : _b, title = _a.title, children = _a.children;
    var tones = {
        neutral: 'bg-gray-50 border-gray-200 text-gray-700',
        info: 'bg-blue-50 border-blue-200 text-blue-900',
        warning: 'bg-amber-50 border-amber-200 text-amber-900',
        success: 'bg-emerald-50 border-emerald-200 text-emerald-900',
    };
    return (<div className={"rounded-xl border p-4 ".concat(tones[tone])}>
            {title && <div className="text-sm font-semibold mb-1">{title}</div>}
            <div className="text-sm leading-relaxed">{children}</div>
        </div>);
};
var Button = function (_a) {
    var children = _a.children, onClick = _a.onClick, _b = _a.type, type = _b === void 0 ? 'button' : _b, _c = _a.variant, variant = _c === void 0 ? 'primary' : _c, _d = _a.disabled, disabled = _d === void 0 ? false : _d, _e = _a.className, className = _e === void 0 ? '' : _e;
    var variants = {
        primary: 'bg-gray-900 hover:bg-gray-800 text-white',
        secondary: 'bg-white hover:bg-gray-50 text-gray-900 border border-gray-200/80',
        danger: 'bg-rose-600 hover:bg-rose-700 text-white',
    };
    return (<button type={type} onClick={onClick} disabled={disabled} className={"px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ".concat(variants[variant], " ").concat(className)}>
            {children}
        </button>);
};
var Input = function (_a) {
    var label = _a.label, value = _a.value, onChange = _a.onChange, _b = _a.type, type = _b === void 0 ? 'text' : _b, placeholder = _a.placeholder, required = _a.required, min = _a.min, step = _a.step, _c = _a.className, className = _c === void 0 ? '' : _c;
    return (<div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <input type={type} value={value} onChange={onChange} placeholder={placeholder} required={required} min={min} step={step} className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors
                 placeholder:text-gray-400"/>
    </div>);
};
var Select = function (_a) {
    var label = _a.label, value = _a.value, onChange = _a.onChange, options = _a.options, _b = _a.className, className = _b === void 0 ? '' : _b;
    return (<div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <select value={value} onChange={onChange} className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors">
            {options.map(function (o) { return <option key={o.value} value={o.value}>{o.label}</option>; })}
        </select>
    </div>);
};
var TextArea = function (_a) {
    var label = _a.label, value = _a.value, onChange = _a.onChange, placeholder = _a.placeholder, _b = _a.rows, rows = _b === void 0 ? 3 : _b, _c = _a.className, className = _c === void 0 ? '' : _c;
    return (<div className={className}>
        {label && <label className="block text-sm font-medium text-gray-800 mb-2">{label}</label>}
        <textarea value={value} onChange={onChange} placeholder={placeholder} rows={rows} className="w-full px-4 py-2.5 border border-gray-200/80 rounded-xl bg-white
                 focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300 transition-colors
                 placeholder:text-gray-400"/>
    </div>);
};
var StatCard = function (_a) {
    var label = _a.label, value = _a.value, hint = _a.hint;
    return (<div className="rounded-2xl border border-gray-200/70 bg-white px-5 py-4 shadow-sm">
        <p className="text-xs font-semibold text-gray-500 tracking-wide uppercase">{label}</p>
        <p className="mt-2 text-2xl font-semibold text-gray-900">{value}</p>
        {hint && <p className="mt-1 text-sm text-gray-600">{hint}</p>}
    </div>);
};
var LoadingSpinner = function () { return (<div className="flex justify-center items-center py-10">
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-gray-200 border-t-gray-900"></div>
    </div>); };
var EmptyState = function (_a) {
    var message = _a.message, _b = _a.icon, icon = _b === void 0 ? '📭' : _b;
    return (<div className="text-center py-10">
        <div className="text-5xl mb-3">{icon}</div>
        <p className="text-gray-600">{message}</p>
    </div>);
};
var TIER_OPTIONS = [
    { value: 'registered', label: 'registered' },
    { value: 'paid', label: 'paid' },
    { value: 'privileged', label: 'privileged' },
];
var USER_TYPE_OPTIONS = [
    { value: 'registered', label: 'registered (free / pilot default)' },
    { value: 'paid', label: 'paid' },
    { value: 'privileged', label: 'privileged (premium)' },
    { value: 'admin', label: 'admin' },
    { value: 'custom', label: 'custom…' },
];
var PROVIDER_LABEL = {
    internal: 'Manual',
    stripe: 'Stripe',
};
function providerLabel(provider) {
    var _a;
    if (!provider)
        return '—';
    return (_a = PROVIDER_LABEL[provider]) !== null && _a !== void 0 ? _a : provider;
}
function formatDateTime(iso) {
    if (!iso)
        return '—';
    var d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
function getDueState(sub, now) {
    if (now === void 0) { now = new Date(); }
    if (sub.status !== 'active')
        return { state: 'inactive', label: 'Inactive' };
    // If there's no next_charge_at, it's simply not scheduled (free/admin, or legacy)
    if (!sub.next_charge_at)
        return { state: 'not_scheduled', label: 'Not scheduled' };
    var due = new Date(sub.next_charge_at);
    if (Number.isNaN(due.getTime()))
        return { state: 'not_scheduled', label: 'Not scheduled' };
    var ms = due.getTime() - now.getTime();
    if (ms <= 0)
        return { state: 'overdue', label: 'Overdue' };
    var days = ms / (1000 * 60 * 60 * 24);
    if (days <= 7)
        return { state: 'due_soon', label: 'Due soon' };
    return { state: 'scheduled', label: 'Scheduled' };
}
var Pill = function (_a) {
    var _b = _a.tone, tone = _b === void 0 ? 'neutral' : _b, children = _a.children;
    var tones = {
        neutral: 'bg-gray-100 text-gray-700 border-gray-200',
        success: 'bg-emerald-50 text-emerald-800 border-emerald-200',
        warning: 'bg-amber-50 text-amber-900 border-amber-200',
        danger: 'bg-rose-50 text-rose-800 border-rose-200',
    };
    return (<span className={"inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ".concat(tones[tone])}>
      {children}
    </span>);
};
function DuePill(_a) {
    var sub = _a.sub;
    var due = getDueState(sub);
    var tone = due.state === 'overdue' ? 'danger' :
        due.state === 'due_soon' ? 'warning' :
            due.state === 'scheduled' ? 'neutral' :
                due.state === 'inactive' ? 'neutral' :
                    'neutral';
    return <Pill tone={tone}>{due.label}</Pill>;
}
var Tabs = function (_a) {
    var active = _a.active, onChange = _a.onChange, items = _a.items;
    return (<div className="flex flex-wrap gap-2">
        {items.map(function (t) {
            var isActive = active === t.id;
            return (<button key={t.id} onClick={function () { return onChange(t.id); }} className={[
                    "px-4 py-2.5 rounded-xl text-sm font-semibold transition-colors border",
                    isActive
                        ? "bg-gray-900 text-white border-gray-900"
                        : "bg-white text-gray-700 border-gray-200/80 hover:bg-gray-50",
                ].join(' ')}>
                    {t.label}
                </button>);
        })}
    </div>);
};
var DividerTitle = function (_a) {
    var title = _a.title, subtitle = _a.subtitle;
    return (<div className="text-center">
        <h1 className="text-4xl md:text-5xl font-semibold text-gray-900 tracking-tight">
            {title}
        </h1>
        <div className="mt-3 flex justify-center">
            <div className="h-1 w-24 bg-gray-900 rounded-full opacity-80"></div>
        </div>
        {subtitle && (<p className="mt-4 text-gray-600 text-base md:text-lg leading-relaxed">
                {subtitle}
            </p>)}
    </div>);
};
// =============================================================================
// Economics Explainers
// =============================================================================
var Details = function (_a) {
    var title = _a.title, children = _a.children;
    return (<details className="rounded-xl border border-gray-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-gray-900">{title}</summary>
        <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">{children}</div>
    </details>);
};
var EconomicsOverview = function (_a) {
    var goTo = _a.goTo;
    return (<Callout tone="neutral" title="Economics: how it works (and what you can control)">
        <div className="space-y-4">
            <div className="text-sm text-gray-700 leading-relaxed">
                There are <strong>two funding lanes</strong>. Which lane is used determines <em>who pays</em> and which counters move.
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="text-sm font-semibold text-gray-900">Lane A — Tier lane ✅ (company-funded)</div>
                    <div className="mt-2 text-sm text-gray-700 space-y-1 leading-relaxed">
                        <div><strong>Used when:</strong> user is within effective tier limits <em>and</em> project (app) budget has funds.</div>
                        <div><strong>Who pays:</strong> <strong>App Budget</strong> (tenant/project wallet).</div>
                        <div><strong>What moves:</strong> tier counters (requests/tokens) are committed.</div>
                        <div className="text-gray-600">
                            Effective tier = base policy (<code>user_type</code>) possibly replaced by a user’s tier override.
                        </div>
                    </div>
                </div>

                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <div className="text-sm font-semibold text-gray-900">Lane B — Paid lane 💳 (user-funded)</div>
                    <div className="mt-2 text-sm text-gray-700 space-y-1 leading-relaxed">
                        <div><strong>Used when:</strong> tier admit is denied (tier quota exceeded) <em>or</em> app budget is empty, but the user has lifetime credits.</div>
                        <div><strong>Who pays:</strong> <strong>User Lifetime Credits</strong> (purchased tokens).</div>
                        <div><strong>What moves:</strong> tier counters are <strong>not</strong> committed (so “quota usage” can look flat).</div>
                    </div>
                </div>
            </div>

            <details className="rounded-xl border border-gray-200 bg-white p-4">
                <summary className="cursor-pointer text-sm font-semibold text-gray-900">
                    Admin levers (what you can change during pilot)
                </summary>
                <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">
                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">1) Base tier (by user_type)</div>
                        <div className="text-gray-700">
                            Configure default limits for <code>registered</code>, <code>paid</code>, <code>privileged</code>, <code>admin</code>.
                        </div>
                        {goTo && (<div className="mt-2">
                                <Button variant="secondary" onClick={function () { return goTo('quotaPolicies'); }}>Open Tier Quota Policies</Button>
                            </div>)}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">2) User Tier Override (replaces base while active)</div>
                        <div className="text-gray-700">
                            Temporary or long override for a specific user. <strong>Not additive</strong>.
                        </div>
                        {goTo && (<div className="mt-2 flex flex-wrap gap-2">
                                <Button variant="secondary" onClick={function () { return goTo('grantTrial'); }}>Grant Trial</Button>
                                <Button variant="secondary" onClick={function () { return goTo('updateTier'); }}>Update Override</Button>
                            </div>)}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">3) User Lifetime Credits (USD → tokens, do not reset)</div>
                        <div className="text-gray-700">
                            Manual “top-up” for user-funded usage when we don’t have payments connected yet.
                        </div>
                        {goTo && (<div className="mt-2">
                                <Button variant="secondary" onClick={function () { return goTo('lifetimeCredits'); }}>Open Lifetime Credits</Button>
                            </div>)}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">4) App Budget (tenant/project wallet)</div>
                        <div className="text-gray-700">
                            Company funds used for tier lane. If it hits zero, tier-funded usage stops.
                        </div>
                        {goTo && (<div className="mt-2">
                                <Button variant="secondary" onClick={function () { return goTo('appBudget'); }}>Open App Budget</Button>
                            </div>)}
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
                        <div className="font-semibold text-gray-900">5) Provider Budget Policies</div>
                        <div className="text-gray-700">
                            Hard caps per provider ($/hour, $/day, $/month) to prevent runaway costs.
                        </div>
                        {goTo && (<div className="mt-2">
                                <Button variant="secondary" onClick={function () { return goTo('budgetPolicies'); }}>Open Budget Policies</Button>
                            </div>)}
                    </div>
                </div>
            </details>

            <details className="rounded-xl border border-gray-200 bg-white p-4">
                <summary className="cursor-pointer text-sm font-semibold text-gray-900">
                    Common confusion: “Why do quota counters not increase?”
                </summary>
                <div className="mt-3 text-sm text-gray-700 leading-relaxed space-y-2">
                    <div>
                        In the <strong>paid lane</strong> the system intentionally does not commit tier counters.
                        So you can see lifetime credits decreasing while “Requests today / Tokens today” remain flat.
                    </div>
                    {goTo && (<div className="mt-2">
                            <Button variant="secondary" onClick={function () { return goTo('quotaBreakdown'); }}>Open Budget Breakdown</Button>
                        </div>)}
                </div>
            </details>
        </div>
    </Callout>);
};
// =============================================================================
// Main Control Plane Admin Component
// =============================================================================
var ControlPlaneAdmin = function () {
    var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t, _u, _v, _w, _x, _y, _z, _0, _1, _2;
    var api = (0, react_1.useMemo)(function () { return new ControlPlaneAPI(); }, []);
    var _3 = (0, react_1.useState)('initializing'), configStatus = _3[0], setConfigStatus = _3[1];
    var _4 = (0, react_1.useState)('grantTrial'), viewMode = _4[0], setViewMode = _4[1];
    // separate loading channels: data loading vs actions
    var _5 = (0, react_1.useState)(false), loadingData = _5[0], setLoadingData = _5[1];
    var _6 = (0, react_1.useState)(false), loadingAction = _6[0], setLoadingAction = _6[1];
    var _7 = (0, react_1.useState)(null), error = _7[0], setError = _7[1];
    var _8 = (0, react_1.useState)(null), success = _8[0], setSuccess = _8[1];
    // Data
    var _9 = (0, react_1.useState)([]), quotaPolicies = _9[0], setQuotaPolicies = _9[1];
    var _10 = (0, react_1.useState)([]), budgetPolicies = _10[0], setBudgetPolicies = _10[1];
    var _11 = (0, react_1.useState)(null), appBudget = _11[0], setAppBudget = _11[1];
    // Forms - Grant Trial
    var _12 = (0, react_1.useState)(''), trialUserId = _12[0], setTrialUserId = _12[1];
    var _13 = (0, react_1.useState)(7), trialDays = _13[0], setTrialDays = _13[1];
    var _14 = (0, react_1.useState)(100), trialRequests = _14[0], setTrialRequests = _14[1];
    var _15 = (0, react_1.useState)(''), trialTokensHour = _15[0], setTrialTokensHour = _15[1];
    var _16 = (0, react_1.useState)(''), trialTokensDay = _16[0], setTrialTokensDay = _16[1];
    var _17 = (0, react_1.useState)('300000000'), trialTokensMonth = _17[0], setTrialTokensMonth = _17[1];
    var _18 = (0, react_1.useState)(''), trialUsdHour = _18[0], setTrialUsdHour = _18[1];
    var _19 = (0, react_1.useState)(''), trialUsdDay = _19[0], setTrialUsdDay = _19[1];
    var _20 = (0, react_1.useState)(''), trialUsdMonth = _20[0], setTrialUsdMonth = _20[1];
    var _21 = (0, react_1.useState)(''), trialNotes = _21[0], setTrialNotes = _21[1];
    // Forms - Update Tier Budget
    var _22 = (0, react_1.useState)(''), updateUserId = _22[0], setUpdateUserId = _22[1];
    var _23 = (0, react_1.useState)(''), updateRequestsDay = _23[0], setUpdateRequestsDay = _23[1];
    var _24 = (0, react_1.useState)(''), updateRequestsMonth = _24[0], setUpdateRequestsMonth = _24[1];
    var _25 = (0, react_1.useState)(''), updateTokensHour = _25[0], setUpdateTokensHour = _25[1];
    var _26 = (0, react_1.useState)(''), updateTokensDay = _26[0], setUpdateTokensDay = _26[1];
    var _27 = (0, react_1.useState)(''), updateTokensMonth = _27[0], setUpdateTokensMonth = _27[1];
    var _28 = (0, react_1.useState)(''), updateUsdHour = _28[0], setUpdateUsdHour = _28[1];
    var _29 = (0, react_1.useState)(''), updateUsdDay = _29[0], setUpdateUsdDay = _29[1];
    var _30 = (0, react_1.useState)(''), updateUsdMonth = _30[0], setUpdateUsdMonth = _30[1];
    var _31 = (0, react_1.useState)(''), updateMaxConcurrent = _31[0], setUpdateMaxConcurrent = _31[1];
    var _32 = (0, react_1.useState)('30'), updateExpiresDays = _32[0], setUpdateExpiresDays = _32[1];
    var _33 = (0, react_1.useState)(''), updateNotes = _33[0], setUpdateNotes = _33[1];
    // Forms - Tier Balance Lookup
    var _34 = (0, react_1.useState)(''), lookupUserId = _34[0], setLookupUserId = _34[1];
    var _35 = (0, react_1.useState)(null), tierBalance = _35[0], setTierBalance = _35[1];
    // Forms - Quota Policy
    var _36 = (0, react_1.useState)('registered'), policyUserType = _36[0], setPolicyUserType = _36[1];
    var _37 = (0, react_1.useState)(''), policyUserTypeCustom = _37[0], setPolicyUserTypeCustom = _37[1];
    var _38 = (0, react_1.useState)(''), policyMaxConcurrent = _38[0], setPolicyMaxConcurrent = _38[1];
    var _39 = (0, react_1.useState)(''), policyRequestsDay = _39[0], setPolicyRequestsDay = _39[1];
    var _40 = (0, react_1.useState)(''), policyRequestsMonth = _40[0], setPolicyRequestsMonth = _40[1];
    var _41 = (0, react_1.useState)(''), policyTokensHour = _41[0], setPolicyTokensHour = _41[1];
    var _42 = (0, react_1.useState)(''), policyTokensDay = _42[0], setPolicyTokensDay = _42[1];
    var _43 = (0, react_1.useState)(''), policyTokensMonth = _43[0], setPolicyTokensMonth = _43[1];
    var _44 = (0, react_1.useState)(''), policyUsdHour = _44[0], setPolicyUsdHour = _44[1];
    var _45 = (0, react_1.useState)(''), policyUsdDay = _45[0], setPolicyUsdDay = _45[1];
    var _46 = (0, react_1.useState)(''), policyUsdMonth = _46[0], setPolicyUsdMonth = _46[1];
    var _47 = (0, react_1.useState)(''), policyNotes = _47[0], setPolicyNotes = _47[1];
    // Forms - Budget Policy
    var _48 = (0, react_1.useState)(''), budgetProvider = _48[0], setBudgetProvider = _48[1];
    var _49 = (0, react_1.useState)(''), budgetUsdHour = _49[0], setBudgetUsdHour = _49[1];
    var _50 = (0, react_1.useState)(''), budgetUsdDay = _50[0], setBudgetUsdDay = _50[1];
    var _51 = (0, react_1.useState)(''), budgetUsdMonth = _51[0], setBudgetUsdMonth = _51[1];
    var _52 = (0, react_1.useState)(''), budgetNotes = _52[0], setBudgetNotes = _52[1];
    // Forms - Quota Breakdown
    var _53 = (0, react_1.useState)(''), breakdownUserId = _53[0], setBreakdownUserId = _53[1];
    var _54 = (0, react_1.useState)('registered'), breakdownUserType = _54[0], setBreakdownUserType = _54[1];
    var _55 = (0, react_1.useState)(null), quotaBreakdown = _55[0], setQuotaBreakdown = _55[1];
    // Forms - Lifetime Credits
    var _56 = (0, react_1.useState)(''), lifetimeUserId = _56[0], setLifetimeUserId = _56[1];
    var _57 = (0, react_1.useState)(''), lifetimeUsdAmount = _57[0], setLifetimeUsdAmount = _57[1];
    var _58 = (0, react_1.useState)(''), lifetimeNotes = _58[0], setLifetimeNotes = _58[1];
    var _59 = (0, react_1.useState)(null), lifetimeBalance = _59[0], setLifetimeBalance = _59[1];
    // App Budget
    var _60 = (0, react_1.useState)(''), appBudgetTopup = _60[0], setAppBudgetTopup = _60[1];
    var _61 = (0, react_1.useState)(''), appBudgetNotes = _61[0], setAppBudgetNotes = _61[1];
    // Subscriptions
    var _62 = (0, react_1.useState)('internal'), subProvider = _62[0], setSubProvider = _62[1];
    var _63 = (0, react_1.useState)(''), subUserId = _63[0], setSubUserId = _63[1];
    var _64 = (0, react_1.useState)('paid'), subTier = _64[0], setSubTier = _64[1];
    var _65 = (0, react_1.useState)(''), subStripePriceId = _65[0], setSubStripePriceId = _65[1];
    var _66 = (0, react_1.useState)(''), subStripeCustomerId = _66[0], setSubStripeCustomerId = _66[1];
    var _67 = (0, react_1.useState)(''), subPriceHint = _67[0], setSubPriceHint = _67[1];
    var _68 = (0, react_1.useState)(''), subLookupUserId = _68[0], setSubLookupUserId = _68[1];
    var _69 = (0, react_1.useState)(null), subscription = _69[0], setSubscription = _69[1];
    var _70 = (0, react_1.useState)(''), subsProviderFilter = _70[0], setSubsProviderFilter = _70[1];
    var _71 = (0, react_1.useState)([]), subsList = _71[0], setSubsList = _71[1];
    var _72 = (0, react_1.useState)(''), breakdownUserTypeCustom = _72[0], setBreakdownUserTypeCustom = _72[1];
    var _73 = (0, react_1.useState)(null), economicsRef = _73[0], setEconomicsRef = _73[1];
    var safeNumber = function (v) { return (typeof v === 'number' && Number.isFinite(v) ? v : 0); };
    var usdToTokens = function (usdText) {
        if (!economicsRef)
            return null;
        var usd = parseFloat(usdText);
        if (!Number.isFinite(usd) || usd <= 0)
            return null;
        return Math.floor(usd / economicsRef.usd_per_token);
    };
    var tokensToUsd = function (tokenText) {
        if (!economicsRef)
            return null;
        var tokens = parseInt(tokenText);
        if (!Number.isFinite(tokens) || tokens <= 0)
            return null;
        return tokens * economicsRef.usd_per_token;
    };
    (0, react_1.useEffect)(function () {
        var initializeSettings = function () { return __awaiter(void 0, void 0, void 0, function () {
            var configReceived, err_1;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        console.log('[Admin] Initializing settings');
                        _a.label = 1;
                    case 1:
                        _a.trys.push([1, 3, , 4]);
                        return [4 /*yield*/, settings.setupParentListener()];
                    case 2:
                        configReceived = _a.sent();
                        console.log('[Admin] Config received?', configReceived);
                        if (configReceived || !window.parent || window.parent === window) {
                            setConfigStatus('ready');
                        }
                        return [3 /*break*/, 4];
                    case 3:
                        err_1 = _a.sent();
                        console.error('[Admin] Error initializing settings:', err_1);
                        setConfigStatus('error');
                        return [3 /*break*/, 4];
                    case 4: return [2 /*return*/];
                }
            });
        }); };
        initializeSettings();
    }, []);
    (0, react_1.useEffect)(function () {
        if (configStatus === 'ready') {
            loadDataForView(viewMode);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [configStatus, viewMode]);
    (0, react_1.useEffect)(function () {
        var loadEconomicsRef = function () { return __awaiter(void 0, void 0, void 0, function () {
            var ref, err_2;
            return __generator(this, function (_a) {
                switch (_a.label) {
                    case 0:
                        if (configStatus !== 'ready')
                            return [2 /*return*/];
                        _a.label = 1;
                    case 1:
                        _a.trys.push([1, 3, , 4]);
                        return [4 /*yield*/, api.getEconomicsReference()];
                    case 2:
                        ref = _a.sent();
                        if (ref.status === 'ok') {
                            setEconomicsRef(ref);
                        }
                        return [3 /*break*/, 4];
                    case 3:
                        err_2 = _a.sent();
                        console.warn('Failed to load economics reference:', err_2);
                        return [3 /*break*/, 4];
                    case 4: return [2 /*return*/];
                }
            });
        }); };
        loadEconomicsRef();
    }, [api, configStatus]);
    var loadDataForView = function (mode) { return __awaiter(void 0, void 0, void 0, function () {
        var needsData, result, result, balance, err_3;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    needsData = ['quotaPolicies', 'budgetPolicies', 'appBudget'].includes(mode);
                    if (!needsData)
                        return [2 /*return*/];
                    setLoadingData(true);
                    setError(null);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 8, 9, 10]);
                    if (!(mode === 'quotaPolicies')) return [3 /*break*/, 3];
                    return [4 /*yield*/, api.listQuotaPolicies()];
                case 2:
                    result = _a.sent();
                    setQuotaPolicies(result.policies || []);
                    return [3 /*break*/, 7];
                case 3:
                    if (!(mode === 'budgetPolicies')) return [3 /*break*/, 5];
                    return [4 /*yield*/, api.listBudgetPolicies()];
                case 4:
                    result = _a.sent();
                    setBudgetPolicies(result.policies || []);
                    return [3 /*break*/, 7];
                case 5:
                    if (!(mode === 'appBudget')) return [3 /*break*/, 7];
                    return [4 /*yield*/, api.getAppBudgetBalance()];
                case 6:
                    balance = _a.sent();
                    setAppBudget(balance);
                    _a.label = 7;
                case 7: return [3 /*break*/, 10];
                case 8:
                    err_3 = _a.sent();
                    setError(err_3.message);
                    console.error('Failed to load data:', err_3);
                    return [3 /*break*/, 10];
                case 9:
                    setLoadingData(false);
                    return [7 /*endfinally*/];
                case 10: return [2 /*return*/];
            }
        });
    }); };
    var clearMessages = function () {
        setError(null);
        setSuccess(null);
    };
    var handleGrantTrial = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var err_4;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.grantTrial({
                            userId: trialUserId,
                            days: trialDays,
                            requestsPerDay: trialRequests,
                            tokensPerHour: trialTokensHour ? parseInt(trialTokensHour) : undefined,
                            tokensPerDay: trialTokensDay ? parseInt(trialTokensDay) : undefined,
                            tokensPerMonth: trialTokensMonth ? parseInt(trialTokensMonth) : undefined,
                            usdPerHour: trialUsdHour ? parseFloat(trialUsdHour) : undefined,
                            usdPerDay: trialUsdDay ? parseFloat(trialUsdDay) : undefined,
                            usdPerMonth: trialUsdMonth ? parseFloat(trialUsdMonth) : undefined,
                            notes: trialNotes,
                        })];
                case 2:
                    _a.sent();
                    setSuccess("Trial granted to ".concat(trialUserId));
                    setTrialUserId('');
                    setTrialNotes('');
                    setTrialTokensHour('');
                    setTrialTokensDay('');
                    setTrialTokensMonth('300000000');
                    setTrialUsdHour('');
                    setTrialUsdDay('');
                    setTrialUsdMonth('');
                    return [3 /*break*/, 5];
                case 3:
                    err_4 = _a.sent();
                    setError(err_4.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleUpdateTierBudget = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var err_5;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.updateTierBudget({
                            userId: updateUserId,
                            requestsPerDay: updateRequestsDay ? parseInt(updateRequestsDay) : undefined,
                            requestsPerMonth: updateRequestsMonth ? parseInt(updateRequestsMonth) : undefined,
                            tokensPerHour: updateTokensHour ? parseInt(updateTokensHour) : undefined,
                            tokensPerDay: updateTokensDay ? parseInt(updateTokensDay) : undefined,
                            tokensPerMonth: updateTokensMonth ? parseInt(updateTokensMonth) : undefined,
                            usdPerHour: updateUsdHour ? parseFloat(updateUsdHour) : undefined,
                            usdPerDay: updateUsdDay ? parseFloat(updateUsdDay) : undefined,
                            usdPerMonth: updateUsdMonth ? parseFloat(updateUsdMonth) : undefined,
                            maxConcurrent: updateMaxConcurrent ? parseInt(updateMaxConcurrent) : undefined,
                            expiresInDays: updateExpiresDays === '' ? null : parseInt(updateExpiresDays),
                            notes: updateNotes
                        })];
                case 2:
                    _a.sent();
                    setSuccess("Tier override updated for ".concat(updateUserId));
                    setUpdateUserId('');
                    setUpdateRequestsDay('');
                    setUpdateRequestsMonth('');
                    setUpdateTokensHour('');
                    setUpdateTokensDay('');
                    setUpdateTokensMonth('');
                    setUpdateUsdHour('');
                    setUpdateUsdDay('');
                    setUpdateUsdMonth('');
                    setUpdateMaxConcurrent('');
                    setUpdateExpiresDays('30');
                    setUpdateNotes('');
                    return [3 /*break*/, 5];
                case 3:
                    err_5 = _a.sent();
                    setError(err_5.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleLookupTierBalance = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var result, err_6;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setTierBalance(null);
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.getTierBalance(lookupUserId)];
                case 2:
                    result = _a.sent();
                    setTierBalance(result);
                    return [3 /*break*/, 5];
                case 3:
                    err_6 = _a.sent();
                    setError(err_6.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleGetQuotaBreakdown = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var result, err_7;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setQuotaBreakdown(null);
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.getUserBudgetBreakdown(breakdownUserId, breakdownUserType)];
                case 2:
                    result = _a.sent();
                    setQuotaBreakdown(result);
                    return [3 /*break*/, 5];
                case 3:
                    err_7 = _a.sent();
                    setError(err_7.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleSetQuotaPolicy = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var err_8;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 4, 5, 6]);
                    return [4 /*yield*/, api.setQuotaPolicy({
                            userType: policyUserType === 'custom' ? policyUserTypeCustom : policyUserType,
                            maxConcurrent: policyMaxConcurrent ? parseInt(policyMaxConcurrent) : undefined,
                            requestsPerDay: policyRequestsDay ? parseInt(policyRequestsDay) : undefined,
                            requestsPerMonth: policyRequestsMonth ? parseInt(policyRequestsMonth) : undefined,
                            tokensPerHour: policyTokensHour ? parseInt(policyTokensHour) : undefined,
                            tokensPerDay: policyTokensDay ? parseInt(policyTokensDay) : undefined,
                            tokensPerMonth: policyTokensMonth ? parseInt(policyTokensMonth) : undefined,
                            usdPerHour: policyUsdHour ? parseFloat(policyUsdHour) : undefined,
                            usdPerDay: policyUsdDay ? parseFloat(policyUsdDay) : undefined,
                            usdPerMonth: policyUsdMonth ? parseFloat(policyUsdMonth) : undefined,
                            notes: policyNotes
                        })];
                case 2:
                    _a.sent();
                    setSuccess("Quota policy set for ".concat(policyUserType));
                    // setPolicyUserType(policyUserType);
                    setPolicyMaxConcurrent('');
                    setPolicyRequestsDay('');
                    setPolicyRequestsMonth('');
                    setPolicyTokensHour('');
                    setPolicyTokensDay('');
                    setPolicyTokensMonth('');
                    setPolicyUsdHour('');
                    setPolicyUsdDay('');
                    setPolicyUsdMonth('');
                    setPolicyUserTypeCustom('');
                    setPolicyNotes('');
                    return [4 /*yield*/, loadDataForView('quotaPolicies')];
                case 3:
                    _a.sent();
                    return [3 /*break*/, 6];
                case 4:
                    err_8 = _a.sent();
                    setError(err_8.message);
                    return [3 /*break*/, 6];
                case 5:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 6: return [2 /*return*/];
            }
        });
    }); };
    var handleSetBudgetPolicy = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var err_9;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 4, 5, 6]);
                    return [4 /*yield*/, api.setBudgetPolicy({
                            provider: budgetProvider,
                            usdPerHour: budgetUsdHour ? parseFloat(budgetUsdHour) : undefined,
                            usdPerDay: budgetUsdDay ? parseFloat(budgetUsdDay) : undefined,
                            usdPerMonth: budgetUsdMonth ? parseFloat(budgetUsdMonth) : undefined,
                            notes: budgetNotes
                        })];
                case 2:
                    _a.sent();
                    setSuccess("Budget policy set for ".concat(budgetProvider));
                    setBudgetProvider('');
                    setBudgetUsdHour('');
                    setBudgetUsdDay('');
                    setBudgetUsdMonth('');
                    setBudgetNotes('');
                    return [4 /*yield*/, loadDataForView('budgetPolicies')];
                case 3:
                    _a.sent();
                    return [3 /*break*/, 6];
                case 4:
                    err_9 = _a.sent();
                    setError(err_9.message);
                    return [3 /*break*/, 6];
                case 5:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 6: return [2 /*return*/];
            }
        });
    }); };
    var handleAddLifetimeCredits = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var uid, result, balance, err_10;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    uid = lifetimeUserId.trim();
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 4, 5, 6]);
                    return [4 /*yield*/, api.addLifetimeCredits(uid, parseFloat(lifetimeUsdAmount), lifetimeNotes)];
                case 2:
                    result = _a.sent();
                    setSuccess("Added $".concat(lifetimeUsdAmount, " (").concat(Number(result.tokens_added).toLocaleString(), " tokens) to ").concat(uid));
                    setLifetimeUsdAmount('');
                    setLifetimeNotes('');
                    return [4 /*yield*/, api.getLifetimeBalance(uid)];
                case 3:
                    balance = _a.sent();
                    setLifetimeBalance(balance);
                    return [3 /*break*/, 6];
                case 4:
                    err_10 = _a.sent();
                    setError(err_10.message);
                    return [3 /*break*/, 6];
                case 5:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 6: return [2 /*return*/];
            }
        });
    }); };
    var handleCheckLifetimeBalance = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var uid, balance, err_11;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLifetimeBalance(null);
                    setLoadingAction(true);
                    uid = lifetimeUserId.trim();
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.getLifetimeBalance(uid)];
                case 2:
                    balance = _a.sent();
                    setLifetimeBalance(balance);
                    return [3 /*break*/, 5];
                case 3:
                    err_11 = _a.sent();
                    setError(err_11.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleTopupAppBudget = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var balance, err_12;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 4, 5, 6]);
                    return [4 /*yield*/, api.topupAppBudget(parseFloat(appBudgetTopup), appBudgetNotes)];
                case 2:
                    _a.sent();
                    setSuccess("App budget topped up: $".concat(appBudgetTopup));
                    setAppBudgetTopup('');
                    setAppBudgetNotes('');
                    return [4 /*yield*/, api.getAppBudgetBalance()];
                case 3:
                    balance = _a.sent();
                    setAppBudget(balance);
                    return [3 /*break*/, 6];
                case 4:
                    err_12 = _a.sent();
                    setError(err_12.message);
                    return [3 /*break*/, 6];
                case 5:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 6: return [2 /*return*/];
            }
        });
    }); };
    var handleCreateSubscription = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var res, err_13;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.createSubscription({
                            userId: subUserId.trim(),
                            tier: subTier,
                            provider: subProvider,
                            stripePriceId: subProvider === 'stripe' ? subStripePriceId.trim() : undefined,
                            stripeCustomerId: subProvider === 'stripe' ? (subStripeCustomerId.trim() || undefined) : undefined,
                            monthlyPriceCentsHint: subProvider === 'stripe' && subPriceHint ? parseInt(subPriceHint) : undefined,
                        })];
                case 2:
                    res = _a.sent();
                    setSuccess(res.message || "Subscription created for ".concat(subUserId));
                    setSubUserId('');
                    setSubStripePriceId('');
                    setSubStripeCustomerId('');
                    setSubPriceHint('');
                    return [3 /*break*/, 5];
                case 3:
                    err_13 = _a.sent();
                    setError(err_13.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleLookupSubscription = function (e) { return __awaiter(void 0, void 0, void 0, function () {
        var res, err_14;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    e.preventDefault();
                    clearMessages();
                    setLoadingAction(true);
                    setSubscription(null);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.getSubscription(subLookupUserId.trim())];
                case 2:
                    res = _a.sent();
                    setSubscription(res.subscription);
                    if (!res.subscription)
                        setSuccess('No subscription found for this user.');
                    return [3 /*break*/, 5];
                case 3:
                    err_14 = _a.sent();
                    setError(err_14.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingAction(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    var handleLoadSubscriptionsList = function () { return __awaiter(void 0, void 0, void 0, function () {
        var res, err_15;
        return __generator(this, function (_a) {
            switch (_a.label) {
                case 0:
                    clearMessages();
                    setLoadingData(true);
                    _a.label = 1;
                case 1:
                    _a.trys.push([1, 3, 4, 5]);
                    return [4 /*yield*/, api.listSubscriptions({
                            provider: subsProviderFilter || undefined,
                            limit: 50,
                            offset: 0,
                        })];
                case 2:
                    res = _a.sent();
                    setSubsList(res.subscriptions || []);
                    return [3 /*break*/, 5];
                case 3:
                    err_15 = _a.sent();
                    setError(err_15.message);
                    return [3 /*break*/, 5];
                case 4:
                    setLoadingData(false);
                    return [7 /*endfinally*/];
                case 5: return [2 /*return*/];
            }
        });
    }); };
    if (configStatus === 'initializing') {
        return (<div className="min-h-screen bg-white flex items-center justify-center p-8">
                <Card className="max-w-lg w-full">
                    <CardBody className="text-center">
                        <LoadingSpinner />
                        <p className="mt-4 text-gray-600">Initializing Control Plane Admin…</p>
                    </CardBody>
                </Card>
            </div>);
    }
    var tabs = [
        { id: 'grantTrial', label: 'Grant Trial' },
        { id: 'updateTier', label: 'Override Tier Limits for User' },
        { id: 'lookup', label: 'Lookup Balance' },
        { id: 'quotaBreakdown', label: 'User Budget Breakdown' },
        { id: 'quotaPolicies', label: 'Tier Limits' },
        { id: 'budgetPolicies', label: 'Project Budget Policies' },
        { id: 'lifetimeCredits', label: 'Lifetime Credits' },
        { id: 'appBudget', label: 'App Budget' },
        { id: 'subscriptions', label: 'Subscriptions' },
    ];
    var usdPerToken = lifetimeBalance && lifetimeBalance.balance_tokens > 0
        ? lifetimeBalance.balance_usd / lifetimeBalance.balance_tokens
        : null;
    var minUsd = usdPerToken && lifetimeBalance
        ? usdPerToken * Number(lifetimeBalance.minimum_required_tokens || 0)
        : null;
    return (<div className="min-h-screen bg-white">
            <div className="max-w-6xl mx-auto px-6 py-10 space-y-8">
                {/* Header */}
                <div className="space-y-6">
                    <DividerTitle title="Control Plane" subtitle="Admin dashboard for user quota policies, tier overrides, purchased credits, and application budget."/>

                    <div className="max-w-4xl mx-auto">
                        <EconomicsOverview goTo={function (tabId) { clearMessages(); setViewMode(tabId); }}/>
                    </div>
                </div>

                {/* Navigation */}
                <div className="max-w-5xl mx-auto">
                    <Tabs active={viewMode} onChange={function (id) { clearMessages(); setViewMode(id); }} items={tabs}/>
                </div>

                {/* Messages */}
                <div className="max-w-5xl mx-auto space-y-3">
                    {success && <Callout tone="success" title="Success">{success}</Callout>}
                    {error && <Callout tone="warning" title="Action failed">{error}</Callout>}
                </div>

                {/* Views */}
                <div className="max-w-5xl mx-auto space-y-6">
                    {/* Grant Trial */}
                    {viewMode === 'grantTrial' && (<Card>
                            <CardHeader title="Grant Trial (temporary tier override)" subtitle="Gives the user a higher tier envelope for a limited time. This OVERRIDES base tier limits — it does not add."/>
                            <CardBody className="space-y-6">
                                <Callout tone="info" title="What this does">
                                    Use for onboarding, marketing trials, or time-limited upgrades. Daily/monthly counters keep resetting while the override is active.
                                </Callout>

                                <form onSubmit={handleGrantTrial} className="space-y-5">
                                    <Input label="User ID *" value={trialUserId} onChange={function (e) { return setTrialUserId(e.target.value); }} placeholder="user123" required/>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Input label="Duration (days)" type="number" value={trialDays.toString()} onChange={function (e) { return setTrialDays(parseInt(e.target.value || '7')); }} min={1}/>
                                        <Input label="Requests / day (override)" type="number" value={trialRequests.toString()} onChange={function (e) { return setTrialRequests(parseInt(e.target.value || '0')); }} min={1}/>
                                        <div>
                                            <Input label="Tokens / hour (override)" type="number" value={trialTokensHour} onChange={function (e) { return setTrialTokensHour(e.target.value); }} min={1}/>
                                            {trialTokensHour && tokensToUsd(trialTokensHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensHour)).toFixed(2)}
                                                </div>)}
                                        </div>
                                    </div>
                                    <div className="text-xs text-gray-500">
                                        USD overrides tokens for the same window.
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div>
                                            <Input label="Tokens / day (override)" type="number" value={trialTokensDay} onChange={function (e) { return setTrialTokensDay(e.target.value); }} min={1}/>
                                            {trialTokensDay && tokensToUsd(trialTokensDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensDay)).toFixed(2)}
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="Tokens / month (override)" type="number" value={trialTokensMonth} onChange={function (e) { return setTrialTokensMonth(e.target.value); }} min={1}/>
                                            {trialTokensMonth && tokensToUsd(trialTokensMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(trialTokensMonth)).toFixed(2)}
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="USD / hour (override)" type="number" value={trialUsdHour} onChange={function (e) { return setTrialUsdHour(e.target.value); }} min={0} step="0.01"/>
                                            {trialUsdHour && usdToTokens(trialUsdHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdHour)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div>
                                            <Input label="USD / day (override)" type="number" value={trialUsdDay} onChange={function (e) { return setTrialUsdDay(e.target.value); }} min={0} step="0.01"/>
                                            {trialUsdDay && usdToTokens(trialUsdDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdDay)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="USD / month (override)" type="number" value={trialUsdMonth} onChange={function (e) { return setTrialUsdMonth(e.target.value); }} min={0} step="0.01"/>
                                            {trialUsdMonth && usdToTokens(trialUsdMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(trialUsdMonth)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                        <div className="text-xs text-gray-500 pt-6">
                                            USD overrides tokens for the same window.
                                        </div>
                                    </div>

                                    <TextArea label="Notes" value={trialNotes} onChange={function (e) { return setTrialNotes(e.target.value); }} placeholder="Welcome trial for new user"/>

                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Granting…' : 'Grant Trial'}
                                    </Button>
                                </form>
                            </CardBody>
                        </Card>)}

                    {/* Update Tier */}
                    {viewMode === 'updateTier' && (<Card>
                            <CardHeader title="Update Tier Override (partial updates)" subtitle="Only fields you provide are updated. Others remain unchanged. This is ideal for fine-tuning an existing override."/>
                            <CardBody className="space-y-6">
                                <Callout tone="warning" title="Override semantics">
                                    This does <strong>not</strong> top-up the base tier. It replaces it for as long as the override is active.
                                </Callout>

                                <form onSubmit={handleUpdateTierBudget} className="space-y-5">
                                    <Input label="User ID *" value={updateUserId} onChange={function (e) { return setUpdateUserId(e.target.value); }} placeholder="user456" required/>

                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <Input label="Requests / day (empty = keep)" type="number" value={updateRequestsDay} onChange={function (e) { return setUpdateRequestsDay(e.target.value); }} placeholder="100"/>
                                        <Input label="Requests / month (empty = keep)" type="number" value={updateRequestsMonth} onChange={function (e) { return setUpdateRequestsMonth(e.target.value); }} placeholder="3000"/>
                                        <div>
                                            <Input label="Tokens / hour (empty = keep)" type="number" value={updateTokensHour} onChange={function (e) { return setUpdateTokensHour(e.target.value); }} placeholder="500000"/>
                                            {updateTokensHour && tokensToUsd(updateTokensHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensHour)).toFixed(2)}
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="Tokens / day (empty = keep)" type="number" value={updateTokensDay} onChange={function (e) { return setUpdateTokensDay(e.target.value); }} placeholder="10000000"/>
                                            {updateTokensDay && tokensToUsd(updateTokensDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensDay)).toFixed(2)}
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="Tokens / month (empty = keep)" type="number" value={updateTokensMonth} onChange={function (e) { return setUpdateTokensMonth(e.target.value); }} placeholder="300000000"/>
                                            {updateTokensMonth && tokensToUsd(updateTokensMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ ${Number(tokensToUsd(updateTokensMonth)).toFixed(2)}
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="USD / hour (empty = keep)" type="number" value={updateUsdHour} onChange={function (e) { return setUpdateUsdHour(e.target.value); }} placeholder="5" min={0} step="0.01"/>
                                            {updateUsdHour && usdToTokens(updateUsdHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdHour)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="USD / day (empty = keep)" type="number" value={updateUsdDay} onChange={function (e) { return setUpdateUsdDay(e.target.value); }} placeholder="50" min={0} step="0.01"/>
                                            {updateUsdDay && usdToTokens(updateUsdDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdDay)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                        <div>
                                            <Input label="USD / month (empty = keep)" type="number" value={updateUsdMonth} onChange={function (e) { return setUpdateUsdMonth(e.target.value); }} placeholder="500" min={0} step="0.01"/>
                                            {updateUsdMonth && usdToTokens(updateUsdMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                    ≈ {Number(usdToTokens(updateUsdMonth)).toLocaleString()} tokens
                                                </div>)}
                                        </div>
                                        <Input label="Max concurrent (empty = keep)" type="number" value={updateMaxConcurrent} onChange={function (e) { return setUpdateMaxConcurrent(e.target.value); }} placeholder="5"/>
                                        <Input label="Expires in days (empty = never)" type="number" value={updateExpiresDays} onChange={function (e) { return setUpdateExpiresDays(e.target.value); }} placeholder="30"/>
                                    </div>

                                    <TextArea label="Notes" value={updateNotes} onChange={function (e) { return setUpdateNotes(e.target.value); }} placeholder="Promotional campaign / compensation / beta program"/>

                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Updating…' : 'Update Override'}
                                    </Button>
                                </form>
                            </CardBody>
                        </Card>)}

                    {/* Lookup */}
                    {viewMode === 'lookup' && (<Card>
                            <CardHeader title="Lookup User Balance" subtitle="Shows active tier override (if any) and purchased lifetime credits (if any)."/>
                            <CardBody className="space-y-6">
                                <form onSubmit={handleLookupTierBalance} className="space-y-4">
                                    <div className="flex gap-3">
                                        <Input value={lookupUserId} onChange={function (e) { return setLookupUserId(e.target.value); }} placeholder="user123" required className="flex-1"/>
                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Loading…' : 'Lookup'}
                                        </Button>
                                    </div>
                                </form>

                                {tierBalance && (<div className="space-y-5">
                                        <Callout tone="info" title="How requests are funded (lane selection)">
                                            <div className="space-y-2">
                                                <div>
                                                    <strong>If Tier Admit passes:</strong> tier allowance is available and tier counters move (tier lane).
                                                </div>
                                                <div>
                                                    <strong>If Tier Admit is denied:</strong> tier allowance is NOT available. Only lifetime credits can fund the request (paid lane),
                                                    and tier counters are not committed.
                                                </div>
                                                <div className="text-gray-600">
                                                    Note: paid lane can still be blocked by <em>concurrency</em> (max_concurrent).
                                                </div>
                                            </div>
                                        </Callout>
                                        <div className="border-t border-gray-200/70 pt-6">
                                            <div className="flex items-baseline justify-between flex-wrap gap-2">
                                                <h3 className="text-2xl font-semibold text-gray-900">
                                                    {tierBalance.user_id}
                                                </h3>
                                                <div className="text-sm text-gray-500">
                                                    {tierBalance.message || ''}
                                                </div>
                                            </div>

                                            {!tierBalance.has_tier_override && !tierBalance.has_lifetime_budget ? (<EmptyState message="No tier override and no purchased credits (base tier only)." icon="📋"/>) : (<div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-5">
                                                    {tierBalance.has_tier_override && tierBalance.tier_override && (<div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                            <div className="flex items-center justify-between">
                                                                <div>
                                                                    <div className="text-sm font-semibold text-gray-900">Tier Override</div>
                                                                    <div className="text-xs text-gray-600 mt-1">
                                                                        Replaces base tier while active
                                                                    </div>
                                                                </div>
                                                                <div className="text-2xl">🎯</div>
                                                            </div>

                                                            <div className="mt-4 space-y-2 text-sm">
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Requests / day</span>
                                                                    <span className="font-semibold text-gray-900">{(_a = tierBalance.tier_override.requests_per_day) !== null && _a !== void 0 ? _a : '—'}</span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / hour</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {(_c = (_b = tierBalance.tier_override.tokens_per_hour) === null || _b === void 0 ? void 0 : _b.toLocaleString()) !== null && _c !== void 0 ? _c : '—'}
                                                                        {tierBalance.tier_override.usd_per_hour != null ? " ($".concat(Number(tierBalance.tier_override.usd_per_hour).toFixed(2), ")") : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / day</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {(_e = (_d = tierBalance.tier_override.tokens_per_day) === null || _d === void 0 ? void 0 : _d.toLocaleString()) !== null && _e !== void 0 ? _e : '—'}
                                                                        {tierBalance.tier_override.usd_per_day != null ? " ($".concat(Number(tierBalance.tier_override.usd_per_day).toFixed(2), ")") : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Tokens / month</span>
                                                                    <span className="font-semibold text-gray-900">
                                                                        {(_g = (_f = tierBalance.tier_override.tokens_per_month) === null || _f === void 0 ? void 0 : _f.toLocaleString()) !== null && _g !== void 0 ? _g : '—'}
                                                                        {tierBalance.tier_override.usd_per_month != null ? " ($".concat(Number(tierBalance.tier_override.usd_per_month).toFixed(2), ")") : ''}
                                                                    </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Expires</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.tier_override.expires_at
                            ? new Date(tierBalance.tier_override.expires_at).toLocaleString()
                            : 'Never'}
                                  </span>
                                                                </div>
                                                                {tierBalance.tier_override.notes && (<div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600 italic">
                                                                        {tierBalance.tier_override.notes}
                                                                    </div>)}
                                                                {tierBalance.tier_override.reference_model && (<div className="pt-2 text-xs text-gray-500">
                                                                        Reference: {tierBalance.tier_override.reference_model}
                                                                    </div>)}
                                                            </div>
                                                        </div>)}

                                                    {tierBalance.has_lifetime_budget && tierBalance.lifetime_budget && (<div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                            <div className="flex items-center justify-between">
                                                                <div>
                                                                    <div className="text-sm font-semibold text-gray-900">Lifetime Credits</div>
                                                                    <div className="text-xs text-gray-600 mt-1">
                                                                        Purchased tokens (do not reset)
                                                                    </div>
                                                                </div>
                                                                <div className="text-2xl">💳</div>
                                                            </div>

                                                            <div className="mt-4 space-y-2 text-sm">
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Gross remaining</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_gross_remaining.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Reserved (in-flight)</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_reserved.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Available now</span>
                                                                    <span className="font-semibold text-gray-900">
                                    {tierBalance.lifetime_budget.tokens_available.toLocaleString()}
                                  </span>
                                                                </div>
                                                                <div className="flex justify-between gap-3">
                                                                    <span className="text-gray-600">Available USD (quoted)</span>
                                                                    <span className="font-semibold text-gray-900">
                                    ${Number(tierBalance.lifetime_budget.available_usd || 0).toFixed(2)}
                                  </span>
                                                                </div>

                                                                <div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600">
                                                                    Reference: {tierBalance.lifetime_budget.reference_model || 'anthropic/claude-sonnet-4-5-20250929'}
                                                                </div>
                                                            </div>
                                                        </div>)}
                                                </div>)}
                                        </div>
                                    </div>)}
                            </CardBody>
                        </Card>)}

                    {/* Quota Breakdown */}
                    {viewMode === 'quotaBreakdown' && (<Card>
                            <CardHeader title="Budget Breakdown" subtitle="Explains base policy vs override vs effective policy, plus current usage and remaining headroom."/>
                            <CardBody className="space-y-6">
                                <Callout tone="neutral" title="How to read this view">
                                    <strong>Effective policy</strong> is what the limiter enforces right now (base tier possibly overridden).
                                    “Remaining” is computed from the effective limits minus current counters.
                                </Callout>
                                <Callout tone="warning" title="Paid lane does NOT show up in these counters">
                                    If the user is being served from <strong>lifetime credits</strong> because tier admit is denied, tier counters are not committed.
                                    That means <strong>requests/tokens here can stay flat</strong> while the user’s lifetime balance goes down.
                                    Use <em>Lifetime Balance</em> to confirm paid-lane spend.
                                </Callout>

                                <form onSubmit={handleGetQuotaBreakdown} className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <Input label="User ID *" value={breakdownUserId} onChange={function (e) { return setBreakdownUserId(e.target.value); }} placeholder="user123" required/>
                                        <Select label="User Type *" value={breakdownUserType} onChange={function (e) { return setBreakdownUserType(e.target.value); }} options={USER_TYPE_OPTIONS}/>

                                        {breakdownUserType === 'custom' && (<Input label="Custom user_type *" value={breakdownUserTypeCustom} onChange={function (e) { return setBreakdownUserTypeCustom(e.target.value); }} placeholder="e.g. enterprise" required/>)}
                                    </div>
                                    <Button type="submit" disabled={loadingAction}>
                                        {loadingAction ? 'Analyzing…' : 'Get Breakdown'}
                                    </Button>
                                </form>

                                {quotaBreakdown && (<div className="space-y-6">
                                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                            <StatCard label="Requests today" value={quotaBreakdown.current_usage.requests_today}/>
                                            <StatCard label="Requests this month" value={quotaBreakdown.current_usage.requests_this_month}/>
                                            <StatCard label="Tokens today" value={"".concat((quotaBreakdown.current_usage.tokens_today / 1000000).toFixed(2), "M")} hint={quotaBreakdown.current_usage.tokens_today_usd != null
                    ? "~$".concat(Number(quotaBreakdown.current_usage.tokens_today_usd).toFixed(2))
                    : 'raw token counters'}/>
                                            <StatCard label="Daily usage %" value={"".concat((_h = quotaBreakdown.remaining.percentage_used) !== null && _h !== void 0 ? _h : 0, "%")}/>
                                        </div>

                                        {/* Credits snapshot */}
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <div className="text-sm font-semibold text-gray-900">Tier envelope</div>
                                                        <div className="text-xs text-gray-600 mt-1">Base → Override → Effective</div>
                                                    </div>
                                                    <div className="text-2xl">📊</div>
                                                </div>

                                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4 text-sm">
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Base</div>
                                                        <div className="text-gray-600">
                                                            req/day: {(_j = quotaBreakdown.base_policy.requests_per_day) !== null && _j !== void 0 ? _j : '—'}<br />
                                                            tok/month: {(_o = (_m = (_l = (_k = quotaBreakdown.base_policy.tokens_per_month) === null || _k === void 0 ? void 0 : _k.toLocaleString) === null || _l === void 0 ? void 0 : _l.call(_k)) !== null && _m !== void 0 ? _m : quotaBreakdown.base_policy.tokens_per_month) !== null && _o !== void 0 ? _o : '—'}
                                                            {quotaBreakdown.base_policy.usd_per_month != null
                    ? " ($".concat(Number(quotaBreakdown.base_policy.usd_per_month).toFixed(2), ")")
                    : ''}
                                                        </div>
                                                    </div>

                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Override</div>
                                                        <div className="text-gray-600">
                                                            {quotaBreakdown.tier_override ? (<>
                                                                    {quotaBreakdown.tier_override.active ? (<Pill tone="success">Active</Pill>) : quotaBreakdown.tier_override.expired ? (<Pill tone="warning">Expired</Pill>) : (<Pill tone="neutral">Inactive</Pill>)}
                                                                    <div className="mt-2">
                                                                        req/day: {(_p = quotaBreakdown.tier_override.limits.requests_per_day) !== null && _p !== void 0 ? _p : '—'}<br />
                                                                        tok/month: {(_t = (_s = (_r = (_q = quotaBreakdown.tier_override.limits.tokens_per_month) === null || _q === void 0 ? void 0 : _q.toLocaleString) === null || _r === void 0 ? void 0 : _r.call(_q)) !== null && _s !== void 0 ? _s : quotaBreakdown.tier_override.limits.tokens_per_month) !== null && _t !== void 0 ? _t : '—'}
                                                                        {quotaBreakdown.tier_override.limits.usd_per_month != null
                        ? " ($".concat(Number(quotaBreakdown.tier_override.limits.usd_per_month).toFixed(2), ")")
                        : ''}<br />
                                                                        expires: {quotaBreakdown.tier_override.expires_at ? new Date(quotaBreakdown.tier_override.expires_at).toLocaleString() : '—'}
                                                                    </div>
                                                                </>) : (<>No override</>)}
                                                        </div>
                                                    </div>

                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="font-semibold text-gray-900 mb-1">Effective</div>
                                                        <div className="text-gray-600">
                                                            req/day: {(_u = quotaBreakdown.effective_policy.requests_per_day) !== null && _u !== void 0 ? _u : '—'}<br />
                                                            tok/month: {(_y = (_x = (_w = (_v = quotaBreakdown.effective_policy.tokens_per_month) === null || _v === void 0 ? void 0 : _v.toLocaleString) === null || _w === void 0 ? void 0 : _w.call(_v)) !== null && _x !== void 0 ? _x : quotaBreakdown.effective_policy.tokens_per_month) !== null && _y !== void 0 ? _y : '—'}
                                                            {quotaBreakdown.effective_policy.usd_per_month != null
                    ? " ($".concat(Number(quotaBreakdown.effective_policy.usd_per_month).toFixed(2), ")")
                    : ''}
                                                        </div>
                                                    </div>
                                                </div>
                                                {quotaBreakdown.reference_model && (<div className="pt-3 text-xs text-gray-500">
                                                        Reference: {quotaBreakdown.reference_model}
                                                    </div>)}
                                            </div>

                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <div className="text-sm font-semibold text-gray-900">Lifetime credits</div>
                                                        <div className="text-xs text-gray-600 mt-1">Gross / reserved / available</div>
                                                    </div>
                                                    <div className="text-2xl">💳</div>
                                                </div>

                                                {!quotaBreakdown.lifetime_credits ? (<div className="mt-4 text-sm text-gray-600">
                                                        No lifetime credits record for this user.
                                                    </div>) : (<div className="mt-4 space-y-2 text-sm">
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Purchased</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_purchased.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Consumed</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_consumed.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Gross remaining</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_gross_remaining.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Reserved</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_reserved.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Available now</span>
                                                            <span className="font-semibold text-gray-900">
                                                                {quotaBreakdown.lifetime_credits.tokens_available.toLocaleString()}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between gap-3">
                                                            <span className="text-gray-600">Available USD (quoted)</span>
                                                            <span className="font-semibold text-gray-900">
                                                                ${Number(quotaBreakdown.lifetime_credits.available_usd || 0).toFixed(2)}
                                                            </span>
                                                        </div>

                                                        <div className="pt-3 border-t border-gray-200/70 text-xs text-gray-600">
                                                            Reference: {quotaBreakdown.lifetime_credits.reference_model}
                                                        </div>
                                                    </div>)}
                                            </div>
                                        </div>

                                        {/* Reservations table */}
                                        {((_z = quotaBreakdown.active_reservations) === null || _z === void 0 ? void 0 : _z.length) > 0 && (<div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="text-sm font-semibold text-gray-900 mb-3">Active credit reservations</div>
                                                <div className="overflow-x-auto">
                                                    <table className="w-full text-sm">
                                                        <thead className="bg-white border-b border-gray-200/70">
                                                        <tr className="text-gray-600">
                                                            <th className="px-4 py-3 text-left font-semibold">Reservation</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Bundle</th>
                                                            <th className="px-4 py-3 text-right font-semibold">Tokens</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Expires</th>
                                                            <th className="px-4 py-3 text-left font-semibold">Notes</th>
                                                        </tr>
                                                        </thead>
                                                        <tbody className="divide-y divide-gray-200/70">
                                                        {quotaBreakdown.active_reservations.map(function (r) {
                        var _a, _b;
                        return (<tr key={r.reservation_id} className="hover:bg-white/70 transition-colors">
                                                                <td className="px-4 py-3 font-semibold text-gray-900">{r.reservation_id}</td>
                                                                <td className="px-4 py-3 text-gray-700">{(_a = r.bundle_id) !== null && _a !== void 0 ? _a : '—'}</td>
                                                                <td className="px-4 py-3 text-right text-gray-700">{Number(r.tokens_reserved || 0).toLocaleString()}</td>
                                                                <td className="px-4 py-3 text-gray-700">{r.expires_at ? new Date(r.expires_at).toLocaleString() : '—'}</td>
                                                                <td className="px-4 py-3 text-gray-600">{(_b = r.notes) !== null && _b !== void 0 ? _b : '—'}</td>
                                                            </tr>);
                    })}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>)}
                                    </div>)}

                            </CardBody>
                        </Card>)}

                    {/* Quota Policies */}
                    {viewMode === 'quotaPolicies' && (<div className="space-y-6">
                            <Card>
                                <CardHeader title="Set Tier Policy" subtitle="Base limits per user_type (global for tenant/project). No bundle_id."/>
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is the default tier envelope for a user class (free/paid/premium). These counters reset on their window (day/month).
                                    </Callout>
                                    {economicsRef && (<div className="text-xs text-gray-500">
                                            Reference: {economicsRef.reference_provider}/{economicsRef.reference_model}
                                        </div>)}

                                    <form onSubmit={handleSetQuotaPolicy} className="space-y-5">
                                        <Select label="User Type *" value={policyUserType} onChange={function (e) { return setPolicyUserType(e.target.value); }} options={USER_TYPE_OPTIONS}/>
                                        {policyUserType === 'custom' && (<Input label="Custom user_type *" value={policyUserTypeCustom} onChange={function (e) { return setPolicyUserTypeCustom(e.target.value); }} placeholder="e.g. enterprise" required/>)}

                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input label="Max concurrent" type="number" value={policyMaxConcurrent} onChange={function (e) { return setPolicyMaxConcurrent(e.target.value); }} placeholder="1"/>
                                            <Input label="Requests / day" type="number" value={policyRequestsDay} onChange={function (e) { return setPolicyRequestsDay(e.target.value); }} placeholder="10"/>
                                            <Input label="Requests / month" type="number" value={policyRequestsMonth} onChange={function (e) { return setPolicyRequestsMonth(e.target.value); }} placeholder="300"/>
                                            <div>
                                                <Input label="Tokens / hour" type="number" value={policyTokensHour} onChange={function (e) { return setPolicyTokensHour(e.target.value); }} placeholder="500000"/>
                                                {policyTokensHour && tokensToUsd(policyTokensHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensHour)).toFixed(2)}
                                                    </div>)}
                                            </div>
                                            <div>
                                                <Input label="Tokens / day" type="number" value={policyTokensDay} onChange={function (e) { return setPolicyTokensDay(e.target.value); }} placeholder="1000000"/>
                                                {policyTokensDay && tokensToUsd(policyTokensDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensDay)).toFixed(2)}
                                                    </div>)}
                                            </div>
                                            <div>
                                                <Input label="Tokens / month" type="number" value={policyTokensMonth} onChange={function (e) { return setPolicyTokensMonth(e.target.value); }} placeholder="30000000"/>
                                                {policyTokensMonth && tokensToUsd(policyTokensMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ ${Number(tokensToUsd(policyTokensMonth)).toFixed(2)}
                                                    </div>)}
                                            </div>
                                            <div>
                                                <Input label="USD / hour" type="number" value={policyUsdHour} onChange={function (e) { return setPolicyUsdHour(e.target.value); }} placeholder="5" min={0} step="0.01"/>
                                                {policyUsdHour && usdToTokens(policyUsdHour) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdHour)).toLocaleString()} tokens
                                                    </div>)}
                                            </div>
                                            <div>
                                                <Input label="USD / day" type="number" value={policyUsdDay} onChange={function (e) { return setPolicyUsdDay(e.target.value); }} placeholder="50" min={0} step="0.01"/>
                                                {policyUsdDay && usdToTokens(policyUsdDay) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdDay)).toLocaleString()} tokens
                                                    </div>)}
                                            </div>
                                            <div>
                                                <Input label="USD / month" type="number" value={policyUsdMonth} onChange={function (e) { return setPolicyUsdMonth(e.target.value); }} placeholder="500" min={0} step="0.01"/>
                                                {policyUsdMonth && usdToTokens(policyUsdMonth) != null && (<div className="text-xs text-gray-500 pt-1">
                                                        ≈ {Number(usdToTokens(policyUsdMonth)).toLocaleString()} tokens
                                                    </div>)}
                                            </div>
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            USD overrides tokens for the same window.
                                        </div>

                                        <TextArea label="Notes" value={policyNotes} onChange={function (e) { return setPolicyNotes(e.target.value); }} placeholder="Free tier limits (global per tenant/project)"/>

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Saving…' : 'Save Policy'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Current Tier Quota Policies" subtitle={"".concat(quotaPolicies.length, " policy records")}/>
                                <CardBody>
                                    {loadingData ? (<LoadingSpinner />) : quotaPolicies.length === 0 ? (<EmptyState message="No tier policies configured." icon="📋"/>) : (<div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">User type</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Max concurrent</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Req/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">Tok/month</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/month</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Notes</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {quotaPolicies.map(function (policy, idx) {
                    var _a, _b, _c, _d, _e, _f, _g, _h;
                    return (<tr key={idx} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{policy.user_type}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{(_a = policy.max_concurrent) !== null && _a !== void 0 ? _a : '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{(_b = policy.requests_per_day) !== null && _b !== void 0 ? _b : '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{(_d = (_c = policy.tokens_per_hour) === null || _c === void 0 ? void 0 : _c.toLocaleString()) !== null && _d !== void 0 ? _d : '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{(_f = (_e = policy.tokens_per_day) === null || _e === void 0 ? void 0 : _e.toLocaleString()) !== null && _f !== void 0 ? _f : '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">{(_h = (_g = policy.tokens_per_month) === null || _g === void 0 ? void 0 : _g.toLocaleString()) !== null && _h !== void 0 ? _h : '—'}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_hour != null ? "$".concat(Number(policy.usd_per_hour).toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_day != null ? "$".concat(Number(policy.usd_per_day).toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_month != null ? "$".concat(Number(policy.usd_per_month).toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-600">{policy.notes || '—'}</td>
                                                    </tr>);
                })}
                                                </tbody>
                                            </table>
                                        </div>)}
                                    {quotaPolicies.length > 0 && quotaPolicies[0].reference_model && (<div className="pt-3 text-xs text-gray-500">
                                            Reference: {quotaPolicies[0].reference_model}
                                        </div>)}
                                </CardBody>
                            </Card>
                        </div>)}

                    {/* Budget Policies */}
                    {viewMode === 'budgetPolicies' && (<div className="space-y-6">
                            <Card>
                                <CardHeader title="Set Provider Budget Policy" subtitle="Spending limits per provider for the tenant/project (no bundle_id)."/>
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is a hard ceiling to prevent runaway costs. Typical usage: cap Anthropic at $/day or $/month.
                                    </Callout>

                                    <form onSubmit={handleSetBudgetPolicy} className="space-y-5">
                                        <Input label="Provider *" value={budgetProvider} onChange={function (e) { return setBudgetProvider(e.target.value); }} placeholder="anthropic" required/>

                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Input label="USD / hour" type="number" step="0.01" value={budgetUsdHour} onChange={function (e) { return setBudgetUsdHour(e.target.value); }} placeholder="10.00"/>
                                            <Input label="USD / day" type="number" step="0.01" value={budgetUsdDay} onChange={function (e) { return setBudgetUsdDay(e.target.value); }} placeholder="200.00"/>
                                            <Input label="USD / month" type="number" step="0.01" value={budgetUsdMonth} onChange={function (e) { return setBudgetUsdMonth(e.target.value); }} placeholder="5000.00"/>
                                        </div>

                                        <TextArea label="Notes" value={budgetNotes} onChange={function (e) { return setBudgetNotes(e.target.value); }} placeholder="Daily spending limit for provider"/>

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Saving…' : 'Save Budget Policy'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Current Budget Policies" subtitle={"".concat(budgetPolicies.length, " policy records")}/>
                                <CardBody>
                                    {loadingData ? (<LoadingSpinner />) : budgetPolicies.length === 0 ? (<EmptyState message="No budget policies configured." icon="💵"/>) : (<div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">Provider</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/hour</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/day</th>
                                                    <th className="px-6 py-4 text-right font-semibold">USD/month</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Notes</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {budgetPolicies.map(function (policy, idx) { return (<tr key={idx} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{policy.provider}</td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_hour != null ? "$".concat(policy.usd_per_hour.toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_day != null ? "$".concat(policy.usd_per_day.toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-right text-gray-700">
                                                            {policy.usd_per_month != null ? "$".concat(policy.usd_per_month.toFixed(2)) : '—'}
                                                        </td>
                                                        <td className="px-6 py-4 text-gray-600">{policy.notes || '—'}</td>
                                                    </tr>); })}
                                                </tbody>
                                            </table>
                                        </div>)}
                                </CardBody>
                            </Card>
                        </div>)}

                    {/* Lifetime Credits */}
                    {viewMode === 'lifetimeCredits' && (<div className="space-y-6">
                            <Card>
                                <CardHeader title="Lifetime Credits (USD → tokens)" subtitle="One-time purchase adds tokens until depleted. These do not reset. Quoted using the backend reference model."/>
                                <CardBody className="space-y-6">
                                    <Callout tone="info" title="Quick interpretation">
                                        “Balance tokens” is what the user can spend. If balance drops below the admission threshold, the system may block paid usage.
                                    </Callout>

                                    <form onSubmit={handleAddLifetimeCredits} className="space-y-5">
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <Input label="User ID *" value={lifetimeUserId} onChange={function (e) { return setLifetimeUserId(e.target.value); }} placeholder="user123" required/>
                                            <Input label="Amount (USD) *" type="number" step="0.01" value={lifetimeUsdAmount} onChange={function (e) { return setLifetimeUsdAmount(e.target.value); }} placeholder="10.00" required/>
                                        </div>

                                        <TextArea label="Purchase Notes" value={lifetimeNotes} onChange={function (e) { return setLifetimeNotes(e.target.value); }} placeholder="Stripe payment ID / invoice / manual purchase note"/>

                                        <div className="flex flex-wrap gap-3">
                                            <Button type="submit" disabled={loadingAction}>
                                                {loadingAction ? 'Processing…' : 'Add Credits'}
                                            </Button>
                                            <Button type="button" variant="secondary" onClick={function () { return handleCheckLifetimeBalance(new Event('submit')); }} disabled={loadingAction || !lifetimeUserId.trim()}>
                                                Check Balance
                                            </Button>
                                            <div className="text-sm text-gray-500 flex items-center">
                                                Reference model: <span className="ml-1 font-semibold text-gray-800">anthropic/claude-sonnet-4-5-20250929</span>
                                            </div>
                                        </div>
                                    </form>
                                </CardBody>
                            </Card>

                            {lifetimeBalance && (<Card>
                                    <CardHeader title={"Current Balance: ".concat(lifetimeBalance.user_id)}/>
                                    <CardBody className="space-y-5">
                                        {lifetimeBalance.has_purchased_credits ? (<>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    <StatCard label="Tokens remaining" value={lifetimeBalance.balance_tokens.toLocaleString()}/>
                                                    <StatCard label="USD equivalent (quoted)" value={"$".concat(Number(lifetimeBalance.balance_usd || 0).toFixed(2))}/>
                                                </div>

                                                {!lifetimeBalance.can_use_budget && (<Callout tone="warning" title="Below admission threshold">
                                                        Needs at least{' '}
                                                        {Number(lifetimeBalance.minimum_required_tokens || 0).toLocaleString()} tokens
                                                        {minUsd != null ? " (\u2248 $".concat(minUsd.toFixed(2), ")") : ''}
                                                        {' '}to run in the paid lane.
                                                    </Callout>)}
                                            </>) : (<EmptyState message="No purchased credits found. This user operates on tier quotas only." icon="💳"/>)}
                                    </CardBody>
                                </Card>)}

                            <Card>
                                <CardHeader title="What the USD conversion means" subtitle="We quote purchases using a fixed reference model so USD→tokens is predictable."/>
                                <CardBody>
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$5.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$10.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                        <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                            <div className="text-xs font-semibold text-gray-500 uppercase">Example</div>
                                            <div className="mt-2 text-2xl font-semibold text-gray-900">$50.00</div>
                                            <div className="mt-2 text-sm text-gray-600">Converted using reference model rate</div>
                                        </div>
                                    </div>
                                </CardBody>
                            </Card>
                        </div>)}

                    {/* App Budget */}
                    {viewMode === 'appBudget' && (<div className="space-y-6">
                            <Card>
                                <CardHeader title="Application Budget" subtitle="Tenant/project wallet used for company-funded spending (typical: tier-funded usage)."/>
                                <CardBody className="space-y-6">
                                    <Callout tone="neutral" title="Meaning">
                                        This is the master budget for the tenant/project. If your policy charges tier-funded usage to the company,
                                        spending will appear here.
                                    </Callout>

                                    {loadingData ? (<LoadingSpinner />) : !appBudget ? (<EmptyState message="No budget data loaded." icon="💰"/>) : (<>
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <StatCard label="Current balance" value={"$".concat(Number(appBudget.balance.balance_usd || 0).toFixed(2))}/>
                                                <StatCard label="Lifetime added" value={"$".concat(Number(appBudget.balance.lifetime_added_usd || 0).toFixed(2))}/>
                                                <StatCard label="Lifetime spent" value={"$".concat(Number(appBudget.balance.lifetime_spent_usd || 0).toFixed(2))}/>
                                            </div>

                                            <div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                <div className="text-sm font-semibold text-gray-900 mb-3">Current month spending</div>
                                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">This hour</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(((_0 = appBudget.current_month_spending) === null || _0 === void 0 ? void 0 : _0.hour) || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">Today</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(((_1 = appBudget.current_month_spending) === null || _1 === void 0 ? void 0 : _1.day) || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                    <div className="rounded-xl bg-white border border-gray-200/70 p-4">
                                                        <div className="text-xs text-gray-500 font-semibold uppercase">This month</div>
                                                        <div className="mt-2 text-2xl font-semibold text-gray-900">
                                                            ${Number(((_2 = appBudget.current_month_spending) === null || _2 === void 0 ? void 0 : _2.month) || 0).toFixed(2)}
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            {appBudget.by_bundle && Object.keys(appBudget.by_bundle).length > 0 && (<div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5">
                                                    <div className="text-sm font-semibold text-gray-900 mb-3">Spending by bundle</div>
                                                    <div className="space-y-3">
                                                        {Object.entries(appBudget.by_bundle).map(function (_a) {
                        var bundleId = _a[0], spending = _a[1];
                        return (<div key={bundleId} className="flex flex-col md:flex-row md:items-center md:justify-between gap-2
                                           rounded-xl bg-white border border-gray-200/70 p-4">
                                                                <div className="font-semibold text-gray-900">{bundleId}</div>
                                                                <div className="text-sm text-gray-600 flex flex-wrap gap-4">
                                                                    <span>Hour: <strong className="text-gray-900">${Number(spending.hour || 0).toFixed(2)}</strong></span>
                                                                    <span>Day: <strong className="text-gray-900">${Number(spending.day || 0).toFixed(2)}</strong></span>
                                                                    <span>Month: <strong className="text-gray-900">${Number(spending.month || 0).toFixed(2)}</strong></span>
                                                                </div>
                                                            </div>);
                    })}
                                                    </div>
                                                </div>)}
                                        </>)}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Top up application budget" subtitle="Adds funds to the tenant/project wallet."/>
                                <CardBody className="space-y-6">
                                    <Callout tone="warning" title="When you need this">
                                        If you’re company-funding tier usage (or any fallback path), you want enough budget to prevent service disruption.
                                    </Callout>

                                    <form onSubmit={handleTopupAppBudget} className="space-y-5">
                                        <Input label="Amount (USD) *" type="number" step="0.01" value={appBudgetTopup} onChange={function (e) { return setAppBudgetTopup(e.target.value); }} placeholder="100.00" required/>
                                        <TextArea label="Notes" value={appBudgetNotes} onChange={function (e) { return setAppBudgetNotes(e.target.value); }} placeholder="Monthly budget allocation"/>
                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Processing…' : 'Add funds'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Budget flow examples" subtitle="Quick mental model for support & ops."/>
                                <CardBody className="space-y-4">
                                    <Callout tone="info" title="Scenario: tier-funded usage">
                                        User operates within effective tier limits → request allowed → company budget is charged (typical policy).
                                    </Callout>
                                    <Callout tone="success" title="Scenario: user-funded fallback">
                                        User exceeds tier → purchased credits present → user credits are charged → app budget not used.
                                    </Callout>
                                    <Callout tone="warning" title="Scenario: mixed / policy-dependent">
                                        Some flows may split charges depending on limiter policy and reservations (in-flight holds).
                                    </Callout>
                                </CardBody>
                            </Card>
                        </div>)}
                    {/* Subscriptions */}
                    {viewMode === 'subscriptions' && (<div className="space-y-6">
                            <Card>
                                <CardHeader title="Create Subscription" subtitle="Creates an internal subscription row or a Stripe subscription (Stripe needs stripe_price_id)."/>
                                <CardBody className="space-y-6">
                                    <form onSubmit={handleCreateSubscription} className="space-y-5">
                                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                            <Select label="Provider" value={subProvider} onChange={function (e) { return setSubProvider(e.target.value); }} options={[
                { value: 'internal', label: 'Manual' },
                { value: 'stripe', label: 'Stripe' },
            ]}/>
                                            <Select label="Tier" value={subTier} onChange={function (e) { return setSubTier(e.target.value); }} options={[
                { value: 'free', label: 'free' },
                { value: 'paid', label: 'paid' },
                { value: 'premium', label: 'premium' },
                { value: 'admin', label: 'admin' },
            ]}/>
                                            <Input label="User ID *" value={subUserId} onChange={function (e) { return setSubUserId(e.target.value); }} placeholder="user123" required/>
                                        </div>

                                        {subProvider === 'stripe' && (<div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <Input label="stripe_price_id *" value={subStripePriceId} onChange={function (e) { return setSubStripePriceId(e.target.value); }} placeholder="price_..." required/>
                                                <Input label="stripe_customer_id (optional)" value={subStripeCustomerId} onChange={function (e) { return setSubStripeCustomerId(e.target.value); }} placeholder="cus_..."/>
                                                <Input label="monthly_price_cents_hint (optional)" type="number" value={subPriceHint} onChange={function (e) { return setSubPriceHint(e.target.value); }} placeholder="2000"/>
                                            </div>)}

                                        <Button type="submit" disabled={loadingAction}>
                                            {loadingAction ? 'Creating…' : 'Create Subscription'}
                                        </Button>
                                    </form>
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Lookup Subscription (by user)" subtitle="Shows the current subscription row stored in user_subscriptions."/>
                                <CardBody className="space-y-6">
                                    <form onSubmit={handleLookupSubscription} className="space-y-4">
                                        <div className="flex gap-3">
                                            <Input value={subLookupUserId} onChange={function (e) { return setSubLookupUserId(e.target.value); }} placeholder="user123" required className="flex-1"/>
                                            <Button type="submit" disabled={loadingAction}>
                                                {loadingAction ? 'Loading…' : 'Lookup'}
                                            </Button>
                                        </div>
                                    </form>

                                    {subscription && (<div className="rounded-2xl border border-gray-200/70 bg-gray-50 p-5 text-sm space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div className="font-semibold text-gray-900">Subscription</div>
                                                <DuePill sub={subscription}/>
                                            </div>

                                            <div className="space-y-2">
                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">billing</span>
                                                    <strong>{providerLabel(subscription.provider)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">tier</span>
                                                    <strong>{subscription.tier}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">status</span>
                                                    <strong>{subscription.status}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">monthly price</span>
                                                    <strong>${(Number(subscription.monthly_price_cents || 0) / 100).toFixed(2)} ({subscription.monthly_price_cents}¢)</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">started</span>
                                                    <strong>{formatDateTime(subscription.started_at)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">last charge</span>
                                                    <strong>{formatDateTime(subscription.last_charged_at)}</strong>
                                                </div>

                                                <div className="flex justify-between">
                                                    <span className="text-gray-600">next charge</span>
                                                    <strong>{formatDateTime(subscription.next_charge_at)}</strong>
                                                </div>

                                                {subscription.provider === 'stripe' && (<>
                                                        <div className="flex justify-between">
                                                            <span className="text-gray-600">stripe_customer_id</span>
                                                            <strong>{subscription.stripe_customer_id || '—'}</strong>
                                                        </div>
                                                        <div className="flex justify-between">
                                                            <span className="text-gray-600">stripe_subscription_id</span>
                                                            <strong>{subscription.stripe_subscription_id || '—'}</strong>
                                                        </div>
                                                    </>)}
                                            </div>

                                            {/* Internal ops */}
                                            {subscription.provider === 'internal' &&
                    subscription.status === 'active' &&
                    (subscription.tier === 'paid' || subscription.tier === 'premium') && (<div className="pt-4 border-t border-gray-200/70 flex flex-wrap items-center justify-between gap-3">
                                                        <div className="text-xs text-gray-600">
                                                            Manual billing: renew will top-up budget and advance next due date.
                                                        </div>

                                                        <Button type="button" variant="secondary" disabled={loadingAction} onClick={function () { return __awaiter(void 0, void 0, void 0, function () {
                        var res, fresh, err_16;
                        return __generator(this, function (_a) {
                            switch (_a.label) {
                                case 0:
                                    clearMessages();
                                    setLoadingAction(true);
                                    _a.label = 1;
                                case 1:
                                    _a.trys.push([1, 4, 5, 6]);
                                    return [4 /*yield*/, api.renewInternalSubscriptionOnce({ userId: subscription.user_id })];
                                case 2:
                                    res = _a.sent();
                                    setSuccess(res.message || "Renewed ".concat(subscription.user_id));
                                    return [4 /*yield*/, api.getSubscription(subscription.user_id)];
                                case 3:
                                    fresh = _a.sent();
                                    setSubscription(fresh.subscription);
                                    return [3 /*break*/, 6];
                                case 4:
                                    err_16 = _a.sent();
                                    setError(err_16.message);
                                    return [3 /*break*/, 6];
                                case 5:
                                    setLoadingAction(false);
                                    return [7 /*endfinally*/];
                                case 6: return [2 /*return*/];
                            }
                        });
                    }); }}>
                                                            {loadingAction ? 'Renewing…' : 'Renew now'}
                                                        </Button>
                                                    </div>)}
                                        </div>)}
                                </CardBody>
                            </Card>

                            <Card>
                                <CardHeader title="Recent Subscriptions" subtitle="Lists last updated subscriptions for this tenant/project." action={<Button variant="secondary" onClick={handleLoadSubscriptionsList} disabled={loadingData}>
                                            {loadingData ? 'Loading…' : 'Refresh'}
                                        </Button>}/>
                                <CardBody className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                        <Select label="Provider filter" value={subsProviderFilter} onChange={function (e) { return setSubsProviderFilter(e.target.value); }} options={[
                { value: '', label: 'all' },
                { value: 'internal', label: 'internal' },
                { value: 'stripe', label: 'stripe' },
            ]}/>
                                    </div>

                                    {subsList.length === 0 ? (<EmptyState message="No subscriptions loaded (click Refresh)." icon="🧾"/>) : (<div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 border-b border-gray-200/70">
                                                <tr className="text-gray-600">
                                                    <th className="px-6 py-4 text-left font-semibold">User</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Billing</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Tier</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Due</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Next</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Last</th>
                                                    <th className="px-6 py-4 text-left font-semibold">Updated</th>
                                                </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-200/70">
                                                {subsList.map(function (s) { return (<tr key={"".concat(s.tenant, ":").concat(s.project, ":").concat(s.user_id)} className="hover:bg-gray-50/70 transition-colors">
                                                        <td className="px-6 py-4 font-semibold text-gray-900">{s.user_id}</td>
                                                        <td className="px-6 py-4 text-gray-700">{providerLabel(s.provider)}</td>
                                                        <td className="px-6 py-4 text-gray-700">{s.tier}</td>
                                                        <td className="px-6 py-4"><DuePill sub={s}/></td>
                                                        <td className="px-6 py-4 text-gray-700">{formatDateTime(s.next_charge_at)}</td>
                                                        <td className="px-6 py-4 text-gray-700">{formatDateTime(s.last_charged_at)}</td>
                                                        <td className="px-6 py-4 text-gray-600">{formatDateTime(s.updated_at)}</td>
                                                    </tr>); })}
                                                </tbody>
                                            </table>
                                        </div>)}
                                </CardBody>
                            </Card>
                        </div>)}
                    {/* Data lists loading indicator (global hint) */}
                    {(viewMode === 'quotaPolicies' || viewMode === 'budgetPolicies' || viewMode === 'appBudget') && loadingData && (<div className="text-center text-sm text-gray-500">Loading…</div>)}


                </div>
            </div>
        </div>);
};
// Render
var rootElement = document.getElementById('root');
if (rootElement) {
    var root = client_1.default.createRoot(rootElement);
    root.render(<ControlPlaneAdmin />);
}
