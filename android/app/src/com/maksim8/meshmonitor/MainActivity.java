package com.maksim8.meshmonitor;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.PowerManager;
import android.provider.Settings;
import android.widget.Toast;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {

    private static final String URL_LAN = "http://192.168.0.x:8080";
    private static final String URL_FUNNEL = "https://your-node.example.ts.net";
    private static final String URL_TAILSCALE = "http://100.x.x.x:8080";

    private static final int REQ_NOTIF = 42;

    private WebView webView;
    private TextView widgetAlphaBtn;
    private TextView intervalBtn;
    private TextView bellBtn;
    private TextView modeBadge;
    private ProgressBar loadingBar;
    private String currentBaseUrl = URL_LAN;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Window window = getWindow();
        if (Build.VERSION.SDK_INT >= 21) {
            window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
            window.setStatusBarColor(Color.parseColor("#070b10"));
            window.setNavigationBarColor(Color.parseColor("#070b10"));
        }

        final LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#070b10"));

        // Edge-to-edge WindowInsets support for Android 15 (API 35) and Android 16 (API 36)
        if (Build.VERSION.SDK_INT >= 30) {
            root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
                @Override
                public WindowInsets onApplyWindowInsets(View v, WindowInsets insets) {
                    Insets sysBars = insets.getInsets(
                            WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                    v.setPadding(sysBars.left, sysBars.top, sysBars.right, sysBars.bottom);
                    return WindowInsets.CONSUMED;
                }
            });
        } else {
            root.setFitsSystemWindows(true);
        }

        // Верхня компактна дворядкова панель: статус на всю ширину + зручні кнопки
        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.VERTICAL);
        topBar.setBackgroundColor(Color.parseColor("#0c131a"));
        topBar.setPadding(dp(10), dp(6), dp(10), dp(8));

        modeBadge = new TextView(this);
        modeBadge.setText("🔍 Пошук зв'язку з Pi…");
        modeBadge.setTextColor(Color.parseColor("#5bffad"));
        modeBadge.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        modeBadge.setTypeface(Typeface.DEFAULT_BOLD);
        modeBadge.setSingleLine(true);
        LinearLayout.LayoutParams badgeParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        badgeParams.bottomMargin = dp(6);
        topBar.addView(modeBadge, badgeParams);

        LinearLayout btnRow = new LinearLayout(this);
        btnRow.setOrientation(LinearLayout.HORIZONTAL);
        btnRow.setGravity(Gravity.CENTER_VERTICAL);

        TextView switchBtn = makeButton("🌐 LAN / Хмара");
        switchBtn.setGravity(Gravity.CENTER);
        switchBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                toggleEndpoint();
            }
        });
        LinearLayout.LayoutParams switchParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.2f);
        switchParams.rightMargin = dp(6);
        btnRow.addView(switchBtn, switchParams);

        TextView reloadBtn = makeButton("🔄 Оновити");
        reloadBtn.setGravity(Gravity.CENTER);
        reloadBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (webView != null) {
                    loadingBar.setVisibility(View.VISIBLE);
                    webView.reload();
                }
            }
        });
        LinearLayout.LayoutParams reloadParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f);
        reloadParams.rightMargin = dp(6);
        btnRow.addView(reloadBtn, reloadParams);

        bellBtn = makeButton(NotifyHelper.isEnabled(this) ? "🔔" : "🔕");
        bellBtn.setGravity(Gravity.CENTER);
        bellBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                boolean on = !NotifyHelper.isEnabled(MainActivity.this);
                NotifyHelper.prefs(MainActivity.this).edit().putBoolean(NotifyHelper.KEY_ENABLED, on).apply();
                bellBtn.setText(on ? "🔔" : "🔕");
                if (on) {
                    enableNotifications();
                    Toast.makeText(MainActivity.this, "Сповіщення радіації та барометра увімкнено", Toast.LENGTH_SHORT).show();
                } else {
                    NotifyHelper.stopAll(MainActivity.this);
                    Toast.makeText(MainActivity.this, "Сповіщення вимкнено", Toast.LENGTH_SHORT).show();
                }
            }
        });
        LinearLayout.LayoutParams bellParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        bellParams.rightMargin = dp(6);
        btnRow.addView(bellBtn, bellParams);

        intervalBtn = makeButton("⏱ " + NotifyHelper.getIntervalMinutes(this) + "хв");
        intervalBtn.setGravity(Gravity.CENTER);
        intervalBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                int cur = NotifyHelper.getIntervalMinutes(MainActivity.this);
                int next = (cur == 5) ? 10 : (cur == 10 ? 15 : 5);
                NotifyHelper.prefs(MainActivity.this).edit().putInt(NotifyHelper.KEY_INTERVAL_MIN, next).apply();
                intervalBtn.setText("⏱ " + next + "хв");
                if (NotifyHelper.isEnabled(MainActivity.this)) {
                    NotifyHelper.scheduleNext(MainActivity.this);
                }
                Toast.makeText(MainActivity.this, "Інтервал сповіщень: " + next + " хв", Toast.LENGTH_SHORT).show();
            }
        });
        LinearLayout.LayoutParams intParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        intParams.rightMargin = dp(6);
        btnRow.addView(intervalBtn, intParams);

        widgetAlphaBtn = makeButton("🪟 " + NotifyHelper.getWidgetAlpha(this) + "%");
        widgetAlphaBtn.setGravity(Gravity.CENTER);
        widgetAlphaBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                int cur = NotifyHelper.getWidgetAlpha(MainActivity.this);
                int next = (cur == 90) ? 70 : (cur == 70 ? 50 : (cur == 50 ? 30 : (cur == 30 ? 0 : 90)));
                NotifyHelper.prefs(MainActivity.this).edit().putInt(NotifyHelper.KEY_WIDGET_ALPHA, next).apply();
                widgetAlphaBtn.setText("🪟 " + next + "%");
                MeshWidgetProvider.updateAll(MainActivity.this);
                Toast.makeText(MainActivity.this,
                        next == 0 ? "Віджет: повністю прозорий" : "Фон віджета: " + next + "%",
                        Toast.LENGTH_SHORT).show();
            }
        });
        btnRow.addView(widgetAlphaBtn);

        topBar.addView(btnRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        root.addView(topBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // Смужка завантаження
        loadingBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        loadingBar.setIndeterminate(true);
        loadingBar.setVisibility(View.VISIBLE);
        root.addView(loadingBar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));

        // WebView
        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#070b10"));
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= 21) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        CookieManager.getInstance().setAcceptCookie(true);

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress >= 95) {
                    loadingBar.setVisibility(View.GONE);
                }
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                loadingBar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (Build.VERSION.SDK_INT >= 21 && request != null && request.isForMainFrame()) {
                    autoSelectAndLoad(true);
                }
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (Build.VERSION.SDK_INT < 23) {
                    autoSelectAndLoad(true);
                }
            }
        });

        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                try {
                    Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    startActivity(i);
                } catch (Exception ignored) {}
            }
        });

        root.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1.0f));

        setContentView(root);

        // Підтримка системного жесту «Назад» для Android 13, 14, 15, 16 (API 33+)
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                    new OnBackInvokedCallback() {
                        @Override
                        public void onBackInvoked() {
                            if (webView != null && webView.canGoBack()) {
                                webView.goBack();
                            } else {
                                finish();
                            }
                        }
                    }
            );
        }

        autoSelectAndLoad(false);

        if (NotifyHelper.isEnabled(this)) {
            enableNotifications();
        }
    }

    /** Запит дозволу на сповіщення (Android 13+), вимкнення оптимізації батареї та запуск служби. */
    private void enableNotifications() {
        NotifyHelper.createChannels(this);
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
            return; // продовжимо в onRequestPermissionsResult
        }
        startMonitorAndAskBattery();
    }

    private void startMonitorAndAskBattery() {
        NotifyHelper.startService(this);
        NotifyHelper.scheduleNext(this);
        if (Build.VERSION.SDK_INT >= 23) {
            try {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                SharedPreferences p = NotifyHelper.prefs(this);
                if (!pm.isIgnoringBatteryOptimizations(getPackageName()) && !p.getBoolean("battery_asked", false)) {
                    p.edit().putBoolean("battery_asked", true).apply();
                    Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                    i.setData(Uri.parse("package:" + getPackageName()));
                    startActivity(i);
                }
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_NOTIF) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startMonitorAndAskBattery();
            } else {
                Toast.makeText(this, "Без дозволу сповіщення не відображатимуться", Toast.LENGTH_LONG).show();
            }
        }
    }

    private TextView makeButton(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextColor(Color.parseColor("#d8fbea"));
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        tv.setPadding(dp(10), dp(5), dp(10), dp(5));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#14212a"));
        bg.setCornerRadius(dp(8));
        bg.setStroke(dp(1), Color.parseColor("#20523f"));
        tv.setBackground(bg);
        return tv;
    }

    private void toggleEndpoint() {
        if (URL_LAN.equals(currentBaseUrl)) {
            currentBaseUrl = URL_FUNNEL;
            modeBadge.setText("☁️ Хмара (Tailscale Funnel)");
            modeBadge.setTextColor(Color.parseColor("#42d9ff"));
        } else {
            currentBaseUrl = URL_LAN;
            modeBadge.setText("🏠 Локальна Wi-Fi (192.168.0.x)");
            modeBadge.setTextColor(Color.parseColor("#5bffad"));
        }
        loadingBar.setVisibility(View.VISIBLE);
        webView.loadUrl(currentBaseUrl);
    }

    private void autoSelectAndLoad(final boolean fallbackFromError) {
        executor.execute(new Runnable() {
            @Override
            public void run() {
                String chosen = URL_FUNNEL;
                String label = "☁️ Хмара (Tailscale Funnel)";
                int color = Color.parseColor("#42d9ff");

                if (!fallbackFromError && isTcpReachable("192.168.0.x", 8080, 900)) {
                    chosen = URL_LAN;
                    label = "🏠 Локальна Wi-Fi (192.168.0.x)";
                    color = Color.parseColor("#5bffad");
                } else if (isTcpReachable("100.x.x.x", 8080, 900)) {
                    chosen = URL_TAILSCALE;
                    label = "🔐 Tailscale VPN (100.x.x.x)";
                    color = Color.parseColor("#58d68d");
                }

                final String finalUrl = chosen;
                final String finalLabel = label;
                final int finalColor = color;

                mainHandler.post(new Runnable() {
                    @Override
                    public void run() {
                        currentBaseUrl = finalUrl;
                        modeBadge.setText(finalLabel);
                        modeBadge.setTextColor(finalColor);
                        loadingBar.setVisibility(View.VISIBLE);
                        webView.loadUrl(finalUrl);
                    }
                });
            }
        });
    }

    private boolean isTcpReachable(String host, int port, int timeoutMs) {
        Socket socket = new Socket();
        try {
            socket.connect(new InetSocketAddress(host, port), timeoutMs);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            try { socket.close(); } catch (Exception ignored) {}
        }
    }

    private int dp(int value) {
        return (int) TypedValue.applyDimension(
                TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics());
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
