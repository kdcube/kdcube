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


def test_client_side_tsx_transpiler_prefers_export_default_over_helper_component():
    html = ClientSideTSXTranspiler.tsx_to_html(
        """
function Card(props: { title: string }) {
    return <div>{props.title}</div>;
}

export default function UserManagementAdmin() {
    return <Card title="Users" />;
}
""",
        title="Test Widget",
    )

    assert "function Card(" in html
    assert "function UserManagementAdmin()" in html
    assert "root.render(<UserManagementAdmin />);" in html
    assert "root.render(<Card />);" not in html
