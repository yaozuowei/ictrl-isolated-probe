import UIKit
import WebKit

/// App-owned surface for locally installed trusted content. No Safari or key transfer.
final class ProbePanel: NSObject, WKNavigationDelegate, WKScriptMessageHandlerWithReply {
    typealias Call = (String, [String: JSONValue]) -> ([String: JSONValue]?, JSONRPCError?)
    private let call: Call
    private var window: UIWindow?
    private var web: WKWebView?
    private var retryButton: UIButton?
    private let url = URL(string: "http://127.0.0.1:18201/local-app")!

    init(call: @escaping Call) {
        self.call = call
        super.init()
        // Bootstrap may have backgrounded the Runner before its window existed.
        // Observe activation only to show UI; never restart a task or bootstrap.
        NotificationCenter.default.addObserver(self, selector: #selector(showWhenActive),
            name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(showWhenActive),
            name: UIScene.didActivateNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func showWhenActive() {
        DispatchQueue.main.async { [weak self] in
            guard !ProbeNativeJob.isBusy else { return }
            self?.show()
        }
    }

    func show() {
        precondition(Thread.isMainThread)
        if let window = window { window.makeKeyAndVisible(); return }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first else { return }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "probe")
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.translatesAutoresizingMaskIntoConstraints = false
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        controller.view.addSubview(web)
        NSLayoutConstraint.activate([
            web.topAnchor.constraint(equalTo: controller.view.safeAreaLayoutGuide.topAnchor),
            web.bottomAnchor.constraint(equalTo: controller.view.safeAreaLayoutGuide.bottomAnchor),
            web.leadingAnchor.constraint(equalTo: controller.view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: controller.view.trailingAnchor)
        ])
        let retry = UIButton(type: .system)
        retry.setTitle("Loading local app…", for: .normal)
        retry.backgroundColor = .systemBackground
        retry.translatesAutoresizingMaskIntoConstraints = false
        retry.addTarget(self, action: #selector(reload), for: .touchUpInside)
        controller.view.addSubview(retry)
        NSLayoutConstraint.activate([
            retry.centerXAnchor.constraint(equalTo: controller.view.centerXAnchor),
            retry.centerYAnchor.constraint(equalTo: controller.view.centerYAnchor),
            retry.widthAnchor.constraint(greaterThanOrEqualToConstant: 260),
            retry.heightAnchor.constraint(equalToConstant: 48)
        ])
        retryButton = retry
        let window = UIWindow(windowScene: scene)
        window.windowLevel = .normal + 1
        window.rootViewController = controller
        self.window = window; self.web = web
        window.makeKeyAndVisible()
        web.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
    }

    @objc func reload() { web?.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)) }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { retryButton?.isHidden = true }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        retryButton?.setTitle("Load failed — tap to retry", for: .normal)
        retryButton?.isHidden = false
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        self.webView(webView, didFailProvisionalNavigation: navigation, withError: error)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        retryButton?.setTitle("Page stopped — tap to reload status", for: .normal)
        retryButton?.isHidden = false
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // A linked external document must never inherit the native control bridge.
        let target = navigationAction.request.url
        decisionHandler(navigationAction.targetFrame?.isMainFrame == true &&
                        target?.scheme == url.scheme && target?.host == url.host &&
                        target?.port == url.port && target?.path == url.path ? .allow : .cancel)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == "http", origin.host == "127.0.0.1", origin.port == 18201,
              message.webView === web, let body = message.body as? [String: Any],
              let method = body["method"] as? String,
              ["native_job_start", "native_job_status", "native_job_cancel"].contains(method),
              let raw = body["params"] as? [String: Any], JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw),
              let params = try? JSONDecoder().decode([String: JSONValue].self, from: data) else {
            replyHandler(nil, "Invalid local panel request"); return
        }
        var result: ([String: JSONValue]?, JSONRPCError?) = (nil, nil)
        let exception = ObjCException.catching { result = self.call(method, params) }
        guard exception == nil, result.1 == nil, let value = result.0,
              let encoded = try? JSONEncoder().encode(value),
              let reply = try? JSONSerialization.jsonObject(with: encoded) else {
            replyHandler(nil, result.1?.message ?? "Native panel call failed; do not retry a start"); return
        }
        replyHandler(reply, nil)
    }
}
