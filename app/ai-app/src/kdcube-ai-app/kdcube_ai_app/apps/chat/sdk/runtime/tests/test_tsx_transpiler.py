from kdcube_ai_app.apps.chat.sdk.viz.tsx_transpiler import ClientSideTSXTranspiler


def test_client_side_tsx_transpiler_adds_render_for_function_component():
    html = ClientSideTSXTranspiler.tsx_to_html(
        """
function PreferencesBrowser() {
    return <div>Hello</div>;
}

export default PreferencesBrowser;
""",
        title="Test Widget",
    )

    assert "function PreferencesBrowser()" in html
    assert "ReactDOM.createRoot(rootElement);" in html
    assert "root.render(<PreferencesBrowser />);" in html


def test_client_side_tsx_transpiler_adds_render_for_const_component():
    html = ClientSideTSXTranspiler.tsx_to_html(
        """
const PreferencesBrowser = () => {
    return <div>Hello</div>;
};

export default PreferencesBrowser;
""",
        title="Test Widget",
    )

    assert "const PreferencesBrowser = () =>" in html
    assert "ReactDOM.createRoot(rootElement);" in html
    assert "root.render(<PreferencesBrowser />);" in html
