import re
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, Page

from kdcube_ai_app.infra.rendering.browser_settings import browser_settings, no_local_browser_error


class ServerSideTSXTranspiler:
    """
    Transpile TSX to HTML using Chromium and Babel Standalone
    Works with all React/TSX features. Only avoid these TypeScript operators:
    - ! (non-null assertion) - use null checks instead
    - as const - use regular const
    - enum - use union types instead
    """

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    def start(self):
        """Initialize browser with Babel"""
        self.playwright = sync_playwright().start()

        # Same remote-browser setting as SharedBrowserService
        # (infra/rendering/shared_browser.py): a deployment can run Chromium
        # in a separate Playwright server instead of inside this process.
        settings = browser_settings()
        ws_endpoint = settings.ws_endpoint
        if ws_endpoint:
            try:
                self.browser = self.playwright.chromium.connect(ws_endpoint)
            except Exception as e:
                print(
                    f"⚠️ Failed to connect to remote Playwright server at "
                    f"{ws_endpoint} ({e}); falling back to a local launch"
                )
                self.browser = None

        if self.browser is None:
            if not settings.local_chromium:
                self.playwright.stop()
                self.playwright = None
                raise no_local_browser_error(ws_endpoint)
            self.browser = self.playwright.chromium.launch(headless=True)

        self.page = self.browser.new_page()

        # Load Babel Standalone
        self.page.set_content("""
        <!DOCTYPE html>
        <html>
        <head>
            <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
        </head>
        <body>
            <script>
                window.transpileTSX = function(tsxCode) {
                    try {
                        const result = Babel.transform(tsxCode, {
                            presets: [
                                ['react', { runtime: 'classic' }],
                                ['typescript', { isTSX: true, allExtensions: true }]
                            ],
                            filename: 'component.tsx'
                        });
                        return { success: true, code: result.code };
                    } catch (error) {
                        return { 
                            success: false, 
                            error: error.message,
                            stack: error.stack 
                        };
                    }
                };
            </script>
        </body>
        </html>
        """)

        # Wait for Babel to load
        self.page.wait_for_function("typeof Babel !== 'undefined'")
        print("✅ Babel Standalone loaded in Chromium")

    def stop(self):
        """Clean up browser resources"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def transpile_tsx(self, tsx_code: str) -> str:
        """
        Transpile TSX to JavaScript

        Args:
            tsx_code: TypeScript React code

        Returns:
            Transpiled JavaScript code

        Raises:
            ValueError: If transpilation fails
        """
        if not self.page:
            raise RuntimeError("Transpiler not started. Call start() first.")

        # Execute transpilation in browser
        result = self.page.evaluate("(tsxCode) => window.transpileTSX(tsxCode)", tsx_code)

        if not result['success']:
            error_msg = result.get('error', 'Unknown error')
            raise ValueError(f"TSX Transpilation failed: {error_msg}")

        compiled_js = result['code']

        # Remove React imports (loaded via CDN in final HTML)
        compiled_js = re.sub(
            r'import\s+React(?:\s*,\s*\{[^}]*\})?\s+from\s+["\']react["\']\s*;?\n?',
            '',
            compiled_js
        )
        compiled_js = re.sub(
            r'import\s+\{([^}]+)\}\s+from\s+["\']react["\']\s*;?\n?',
            '',
            compiled_js
        )
        compiled_js = re.sub(
            r'import\s+ReactDOM\s+from\s+["\']react-dom/client["\']\s*;?\n?',
            '',
            compiled_js
        )

        return compiled_js

    def tsx_to_html(self, tsx_code: str, title: str = "TSX Component") -> str:
        """
        Convert TSX to standalone HTML

        Args:
            tsx_code: TypeScript React code
            title: Page title

        Returns:
            Complete HTML document
        """
        compiled_js = self.transpile_tsx(tsx_code)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    
    <!-- React 18 -->
    <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
    <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
    
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background-color: #f9fafb;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
        }}
    </style>
</head>
<body>
    <div id="root"></div>

    <script>
        const {{ useState, useEffect, useRef, useMemo, useCallback }} = React;
        
        {compiled_js}
    </script>
</body>
</html>"""

        return html

class ClientSideTSXTranspiler:
    """
    Generate HTML with embedded TSX that compiles in the browser
    No Chromium or Node.js needed - pure client-side!
    """

    TAILWIND_URL = "https://cdn.tailwindcss.com/3.4.17"
    REACT_URL = "https://unpkg.com/react@18.3.1/umd/react.production.min.js"
    REACT_DOM_URL = "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"
    CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    CHART_DATALABELS_URL = "https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0"
    BABEL_URL = "https://unpkg.com/@babel/standalone@7.28.5/babel.min.js"

    @staticmethod
    def tsx_to_html(tsx_code: str, title: str = "TSX Component") -> str:
        """
        Embed TSX code in HTML with Babel Standalone
        Browser will compile TSX on load

        Args:
            tsx_code: TypeScript React code
            title: Page title

        Returns:
            Complete HTML document with embedded TSX
        """

        import re

        # Prefer the explicitly exported component over the first helper component
        # found in the file. Many widgets define helper components (Card, Button,
        # etc.) before the main exported component.
        export_default_match = re.search(
            r'export\s+default\s+function\s+([A-Z][a-zA-Z0-9_]*)\s*\(',
            tsx_code,
        )
        export_default_name = export_default_match.group(1) if export_default_match else None
        if not export_default_name:
            export_ref_match = re.search(
                r'export\s+default\s+([A-Z][a-zA-Z0-9_]*)\s*;',
                tsx_code,
            )
            export_default_name = export_ref_match.group(1) if export_ref_match else None

        # Remove React hook declarations from TSX code (we provide them in the wrapper)
        tsx_code = re.sub(r'const\s*\{[^}]*\}\s*=\s*React\s*;?\s*\n?', '', tsx_code)

        # Remove any import statements
        tsx_code = re.sub(r'import\s+.*from\s+["\']react["\'].*\n?', '', tsx_code)
        tsx_code = re.sub(r'import\s+.*from\s+["\']react-dom.*\n?', '', tsx_code)

        # Remove export default
        tsx_code = re.sub(r'export\s+default\s+', '', tsx_code)

        # Find main component name
        component_match = re.search(
            r'(?:const\s+([A-Z][a-zA-Z0-9]*)\s*[=:]|function\s+([A-Z][a-zA-Z0-9]*)\s*\()',
            tsx_code,
        )
        main_component = export_default_name
        if not main_component and component_match:
            main_component = component_match.group(1) or component_match.group(2)

        # Check if already has render logic
        has_render = 'ReactDOM.createRoot' in tsx_code

        # Add render logic if missing
        if not has_render and main_component:
            # Remove trailing component reference like "CostModel;"
            pattern = r'\n\s*' + main_component + r'\s*;\s*$'
            tsx_code = re.sub(pattern, '', tsx_code)

            # Add render code
            render_code = f"\n\nconst rootElement = document.getElementById('root');\nif (rootElement) {{\n    const root = ReactDOM.createRoot(rootElement);\n    root.render(<{main_component} />);\n}}"
            tsx_code += render_code

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>

    <script>
        window.__kdcubeShowWidgetError = function(message, detail) {{
            const loading = document.getElementById('loading');
            const errorDisplay = document.getElementById('error-display');
            if (loading) loading.style.display = 'none';
            if (errorDisplay) {{
                errorDisplay.style.display = 'block';
                errorDisplay.textContent = message + (detail ? '\\n\\n' + detail : '');
            }} else {{
                window.__kdcubeWidgetPendingError = {{ message: message, detail: detail || '' }};
            }}
            console.error('[kdcube widget]', message, detail || '');
        }};
        window.__kdcubeFlushWidgetError = function() {{
            if (!window.__kdcubeWidgetPendingError) return;
            const pending = window.__kdcubeWidgetPendingError;
            window.__kdcubeWidgetPendingError = null;
            window.__kdcubeShowWidgetError(pending.message, pending.detail);
        }};
        window.addEventListener('error', function(e) {{
            const message = e && e.message ? e.message : 'Script/resource load error';
            const detailParts = [];
            if (e && e.filename) detailParts.push('file: ' + e.filename);
            if (e && e.lineno) detailParts.push('line: ' + e.lineno + (e.colno ? ':' + e.colno : ''));
            if (e && e.error && e.error.stack) detailParts.push(e.error.stack);
            window.__kdcubeShowWidgetError('TSX widget error: ' + message, detailParts.join('\\n'));
        }});
        window.addEventListener('unhandledrejection', function(e) {{
            const reason = e && e.reason;
            const detail = reason && reason.stack ? reason.stack : String(reason || '');
            window.__kdcubeShowWidgetError('TSX widget async error', detail);
        }});
    </script>

    <!-- Tailwind CSS -->
    <script src="{ClientSideTSXTranspiler.TAILWIND_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load Tailwind runtime', this.src)"></script>
    
    <!-- React 18 -->
    <script crossorigin src="{ClientSideTSXTranspiler.REACT_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load React runtime', this.src)"></script>
    <script crossorigin src="{ClientSideTSXTranspiler.REACT_DOM_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load ReactDOM runtime', this.src)"></script>
    
    <!-- Chart.js -->
    <script src="{ClientSideTSXTranspiler.CHART_JS_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load Chart.js runtime', this.src)"></script>
    <script src="{ClientSideTSXTranspiler.CHART_DATALABELS_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load Chart.js datalabels plugin', this.src)"></script>
    <!-- Babel Standalone -->
    <script src="{ClientSideTSXTranspiler.BABEL_URL}" onerror="window.__kdcubeShowWidgetError('Failed to load Babel TypeScript runtime', this.src)"></script>
    <!-- Configure Babel for TypeScript -->
    <script>
        if (typeof Babel !== 'undefined') {{
            Babel.registerPreset('custom-typescript', {{
                presets: [
                    [Babel.availablePresets['typescript'], {{ 
                        isTSX: true, 
                        allExtensions: true 
                    }}],
                    [Babel.availablePresets['react'], {{
                        runtime: 'classic'
                    }}]
                ]
            }});
        }} else {{
            window.__kdcubeShowWidgetError('Babel TypeScript runtime is not available', '{ClientSideTSXTranspiler.BABEL_URL}');
        }}
    </script>
    
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background-color: #f9fafb;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
        }}
        #loading {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
        #error-display {{
            display: none;
            margin: 20px auto;
            max-width: 800px;
            padding: 20px;
            background: #fee;
            border: 2px solid #c00;
            border-radius: 8px;
            color: #c00;
            font-family: monospace;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
    <div id="loading">Loading component...</div>
    <div id="root"></div>
    <div id="error-display"></div>

    <script>
        window.__kdcubeFlushWidgetError();
    </script>

    <script type="text/babel" data-presets="custom-typescript">
        if (typeof React === 'undefined') {{
            throw new Error('React runtime is not available');
        }}
        if (typeof ReactDOM === 'undefined') {{
            throw new Error('ReactDOM runtime is not available');
        }}
        const {{ useState, useEffect, useRef, useMemo, useCallback }} = React;
        
        // Hide loading indicator
        const loading = document.getElementById('loading');
        if (loading) loading.style.display = 'none';
        
        {tsx_code}
    </script>
</body>
</html>"""

        return html

def server_side_example_usage():
    """Example of using the transpiler directly"""

    transpiler = ServerSideTSXTranspiler()
    transpiler.start()

    try:
        tsx_code = """
        interface DashboardProps {
            title: string;
        }
        
        const Dashboard: React.FC<DashboardProps> = ({ title }) => {
            const [count, setCount] = useState<number>(0);
            const [items, setItems] = useState<string[]>(['Item 1', 'Item 2']);
            
            const handleAdd = () => {
                setItems([...items, `Item ${items.length + 1}`]);
            };
            
            return (
                <div className="max-w-4xl mx-auto p-6">
                    <div className="bg-white rounded-lg shadow-lg p-6">
                        <h1 className="text-3xl font-bold text-gray-800 mb-4">
                            {title}
                        </h1>
                        
                        <div className="space-y-4">
                            <div className="flex items-center gap-4">
                                <button
                                    onClick={() => setCount(count + 1)}
                                    className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
                                >
                                    Count: {count}
                                </button>
                                
                                <button
                                    onClick={handleAdd}
                                    className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600"
                                >
                                    Add Item
                                </button>
                            </div>
                            
                            <div className="border rounded p-4">
                                <h2 className="font-semibold mb-2">Items:</h2>
                                <ul className="list-disc list-inside">
                                    {items.map((item, idx) => (
                                        <li key={idx}>{item}</li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    </div>
                </div>
            );
        };
        
        const rootElement = document.getElementById('root');
        if (rootElement) {
            const root = ReactDOM.createRoot(rootElement);
            root.render(<Dashboard title="My TSX Dashboard" />);
        }
        """

        # Convert to HTML
        html = transpiler.tsx_to_html(tsx_code, "Dashboard Example")

        # Save to file
        with open('dashboard.html', 'w') as f:
            f.write(html)

        print("✅ Successfully generated dashboard.html")
        print("📂 Open dashboard.html in your browser to view")

    finally:
        transpiler.stop()

def client_side_example_usage(filepath: str = None):
    """Example of generating client-side HTML"""

    tsx_code = """
    interface DashboardProps {
        title: string;
    }
    
    const Dashboard: React.FC<DashboardProps> = ({ title }) => {
        const [count, setCount] = useState<number>(0);
        const [users, setUsers] = useState<number>(100);
        
        const stats = useMemo(() => {
            return {
                total: count * users,
                average: users > 0 ? count / users : 0
            };
        }, [count, users]);
        
        return (
            <div className="max-w-4xl mx-auto p-6">
                <div className="bg-white rounded-lg shadow-lg p-6">
                    <h1 className="text-3xl font-bold text-gray-800 mb-6">
                        {title}
                    </h1>
                    
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                        <div className="bg-blue-50 p-4 rounded-lg">
                            <div className="text-sm text-gray-600">Count</div>
                            <div className="text-2xl font-bold text-blue-600">{count}</div>
                        </div>
                        
                        <div className="bg-green-50 p-4 rounded-lg">
                            <div className="text-sm text-gray-600">Users</div>
                            <div className="text-2xl font-bold text-green-600">{users}</div>
                        </div>
                        
                        <div className="bg-purple-50 p-4 rounded-lg">
                            <div className="text-sm text-gray-600">Total</div>
                            <div className="text-2xl font-bold text-purple-600">{stats.total}</div>
                        </div>
                        
                        <div className="bg-orange-50 p-4 rounded-lg">
                            <div className="text-sm text-gray-600">Average</div>
                            <div className="text-2xl font-bold text-orange-600">
                                {stats.average.toFixed(2)}
                            </div>
                        </div>
                    </div>
                    
                    <div className="flex gap-4">
                        <button
                            onClick={() => setCount(count + 1)}
                            className="px-4 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600"
                        >
                            Increment Count
                        </button>
                        
                        <button
                            onClick={() => setUsers(users + 10)}
                            className="px-4 py-2 bg-green-500 text-white rounded-md hover:bg-green-600"
                        >
                            Add 10 Users
                        </button>
                    </div>
                </div>
            </div>
        );
    };
    
    const rootElement = document.getElementById('root');
    if (rootElement) {
        const root = ReactDOM.createRoot(rootElement);
        root.render(<Dashboard title="Client-Side TSX Dashboard" />);
    }
    """

    if filepath:
        with open(filepath, "r", encoding="utf-8") as f:
            tsx_code = f.read()

    # Generate HTML
    html = ClientSideTSXTranspiler.tsx_to_html(tsx_code, "Dashboard Example")

    # Save to file
    with open('dashboard-client.html', 'w') as f:
        f.write(html)

    print("✅ Generated dashboard-client.html")
    print("📂 Open in browser - TSX compiles on page load!")

if __name__  == "__main__":
    # server_side_example_usage()
    filepath = "re.tsx"
    client_side_example_usage(filepath)
