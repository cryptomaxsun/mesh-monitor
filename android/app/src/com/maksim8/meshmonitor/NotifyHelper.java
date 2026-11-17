package com.maksim8.meshmonitor;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.SystemClock;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import org.json.JSONObject;

/** Отримання даних з Pi, постійне сповіщення, попередження та оновлення віджета. */
public final class NotifyHelper {

    public static final String CHANNEL_STATUS = "mesh_status";
    public static final String CHANNEL_ALARM = "mesh_alarm";
    public static final int NOTIF_ID = 1001;
    public static final int ALARM_RAD_ID = 1002;
    public static final int ALARM_STORM_ID = 1003;
    public static final int ALARM_BAT_ID = 1004;
    public static final int ALARM_CPU_ID = 1005;

    static final String PREFS = "mesh_prefs";
    static final String KEY_ENABLED = "notify_enabled";
    static final String KEY_INTERVAL_MIN = "interval_min";
    static final String KEY_LAST_TITLE = "last_title";
    static final String KEY_LAST_TEXT = "last_text";
    static final String KEY_LAST_ICON = "last_icon";
    static final String KEY_LAST_ARROW = "last_arrow";
    static final String KEY_WAS_ALARM = "last_alarm";
    static final String KEY_WAS_STORM = "last_storm";
    static final String KEY_WAS_BAT = "last_bat";
    static final String KEY_WAS_CPU = "last_cpu";

    static final String KEY_WIDGET_RAD = "w_rad";
    static final String KEY_WIDGET_BARO = "w_baro";
    static final String KEY_WIDGET_TEMP = "w_temp";
    static final String KEY_WIDGET_TIME = "w_time";
    static final String KEY_WIDGET_ALPHA = "w_alpha";

    private static final String[] BASES = {
            "http://192.168.0.x:8080",
            "http://100.x.x.x:8080",
            "https://your-node.example.ts.net"
    };

    private NotifyHelper() {}

    public static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static boolean isEnabled(Context c) {
        return prefs(c).getBoolean(KEY_ENABLED, true);
    }

    public static int getIntervalMinutes(Context c) {
        int m = prefs(c).getInt(KEY_INTERVAL_MIN, 5);
        return (m == 5 || m == 10 || m == 15) ? m : 5;
    }

    public static int getWidgetAlpha(Context c) {
        int a = prefs(c).getInt(KEY_WIDGET_ALPHA, 70);
        return (a == 90 || a == 70 || a == 50 || a == 30 || a == 0) ? a : 70;
    }

    public static void createChannels(Context c) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel status = new NotificationChannel(
                CHANNEL_STATUS, "Радіація та барометр", NotificationManager.IMPORTANCE_LOW);
        status.setDescription("Постійне сповіщення з поточним фоном радіації і тенденцією тиску");
        status.setShowBadge(false);
        status.setSound(null, null);
        status.enableVibration(false);
        nm.createNotificationChannel(status);

        NotificationChannel alarm = new NotificationChannel(
                CHANNEL_ALARM, "Важливі попередження", NotificationManager.IMPORTANCE_HIGH);
        alarm.setDescription("Радіація >= 0.30, штормове падіння тиску, розряд батареї, перегрів Pi");
        alarm.enableVibration(true);
        nm.createNotificationChannel(alarm);
    }

    /** Завантажує /api/notify з першої доступної адреси (LAN → Tailscale → Funnel). */
    public static JSONObject fetch() {
        for (String base : BASES) {
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(base + "/api/notify").openConnection();
                conn.setConnectTimeout(base.startsWith("https") ? 8000 : 2500);
                conn.setReadTimeout(8000);
                conn.setUseCaches(false);
                if (conn.getResponseCode() != 200) continue;
                BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                return new JSONObject(sb.toString());
            } catch (Exception ignored) {
            } finally {
                if (conn != null) conn.disconnect();
            }
        }
        return null;
    }

    /** Отримати дані та оновити сповіщення й віджет (викликати НЕ з головного потоку). */
    public static void refreshNow(Context c) {
        JSONObject j = fetch();
        SharedPreferences p = prefs(c);
        SharedPreferences.Editor e = p.edit();
        if (j != null) {
            double usvh = j.optDouble("usvh", Double.NaN);
            String radStatus = j.optString("rad_status", "");
            String arrow = j.optString("trend_arrow", "…");
            String trendText = j.optString("trend_text", "");
            double delta = j.optDouble("trend_delta", Double.NaN);
            double hours = j.optDouble("trend_hours", Double.NaN);
            double pressure = j.optDouble("pressure", Double.NaN);
            double cpuTemp = j.optDouble("cpu_temp", Double.NaN);
            int lowBatPct = j.optInt("low_bat_pct", 0);
            boolean alarm = j.optBoolean("alarm", false);
            boolean storm = j.optBoolean("storm_alert", false);
            boolean batAlert = j.optBoolean("bat_alert", false);
            boolean cpuAlert = j.optBoolean("cpu_alert", false);
            String time = j.optString("server_time", "");

            String radVal = Double.isNaN(usvh) ? "—" : String.format(Locale.US, "%.3f", usvh);
            String title = "☢️ " + radVal + " мкЗв/год · " + radStatus;
            StringBuilder text = new StringBuilder("🧭 ").append(arrow).append(' ').append(trendText);
            if (!Double.isNaN(delta) && !Double.isNaN(hours)) {
                text.append(String.format(Locale.US, " (%+.1f hPa за %s год)", delta, trimNum(hours)));
            }
            if (!Double.isNaN(pressure)) {
                text.append(String.format(Locale.US, " · %.1f hPa", pressure));
            }
            if (!time.isEmpty()) text.append(" · ").append(time);

            e.putString(KEY_LAST_TITLE, title);
            e.putString(KEY_LAST_TEXT, text.toString());
            e.putString(KEY_LAST_ICON, Double.isNaN(usvh) ? "—" : String.format(Locale.US, "%.2f", usvh));
            e.putString(KEY_LAST_ARROW, arrow);

            // Дані для віджета
            e.putString(KEY_WIDGET_RAD, "☢️ " + radVal + " мкЗв/год");
            String baroW = "🧭 " + arrow + " " + trendText
                    + (!Double.isNaN(delta) ? String.format(Locale.US, " (%+.1f)", delta) : "");
            e.putString(KEY_WIDGET_BARO, baroW);
            e.putString(KEY_WIDGET_TEMP, Double.isNaN(pressure) ? "— hPa" : String.format(Locale.US, "%.1f hPa", pressure));
            e.putString(KEY_WIDGET_TIME, time.isEmpty() ? "—" : time);

            checkAlert(c, p, e, alarm, KEY_WAS_ALARM, ALARM_RAD_ID,
                    "🚨 ТРИВОГА! Радіація " + radVal + " мкЗв/год", "Фон перевищує 0.30 мкЗв/год");
            checkAlert(c, p, e, storm, KEY_WAS_STORM, ALARM_STORM_ID,
                    "⛈️ Штормове попередження",
                    String.format(Locale.US, "Тиск різко падає (%+.1f hPa)", delta));
            checkAlert(c, p, e, batAlert, KEY_WAS_BAT, ALARM_BAT_ID,
                    "🪫 Низький заряд батареї (" + lowBatPct + "%)",
                    "Час зарядити дозиметр або ноду Meshtastic");
            checkAlert(c, p, e, cpuAlert, KEY_WAS_CPU, ALARM_CPU_ID,
                    String.format(Locale.US, "🔥 Перегрів Raspberry Pi (%.1f °C)", cpuTemp),
                    "Температура процесора досягла 75 °C");

            e.apply();
        } else {
            String prev = p.getString(KEY_LAST_TEXT, "Немає зв'язку з Pi");
            if (!prev.contains("немає зв'язку") && !prev.contains("Немає зв'язку")) {
                prev = prev + " · немає зв'язку";
            }
            e.putString(KEY_LAST_TEXT, prev);
            e.apply();
        }

        MeshWidgetProvider.updateAll(c);

        if (isEnabled(c)) {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            try {
                nm.notify(NOTIF_ID, buildNotification(c));
            } catch (SecurityException ignored) {
            }
        }
    }

    private static void checkAlert(Context c, SharedPreferences p, SharedPreferences.Editor e,
                                   boolean active, String key, int id, String title, String msg) {
        boolean was = p.getBoolean(key, false);
        e.putBoolean(key, active);
        if (active && !was) showAlert(c, id, title, msg);
    }

    private static String trimNum(double v) {
        if (v == Math.rint(v)) return String.valueOf((long) v);
        return String.format(Locale.US, "%.1f", v);
    }

    public static Notification buildNotification(Context c) {
        createChannels(c);
        SharedPreferences p = prefs(c);
        String title = p.getString(KEY_LAST_TITLE, "☢️ Радіація: завантаження…");
        String text = p.getString(KEY_LAST_TEXT, "🧭 Барометр: завантаження…");

        Intent open = new Intent(c, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(c, 0, open, piFlags());

        Intent refreshIntent = new Intent(c, RefreshReceiver.class);
        PendingIntent refreshPi = PendingIntent.getBroadcast(c, 11, refreshIntent, piFlags());

        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, CHANNEL_STATUS)
                : new Notification.Builder(c);
        b.setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(true)
                .setWhen(System.currentTimeMillis())
                .setCategory(Notification.CATEGORY_STATUS)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setColor(Color.parseColor("#5bffad"))
                .addAction(R.drawable.ic_stat, "🔄 Оновити", refreshPi);
        if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_LOW);
        if (Build.VERSION.SDK_INT >= 31) {
            b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        if (Build.VERSION.SDK_INT >= 23) {
            b.setSmallIcon(Icon.createWithBitmap(statusIcon(
                    p.getString(KEY_LAST_ICON, "☢"), p.getString(KEY_LAST_ARROW, ""))));
        } else {
            b.setSmallIcon(R.drawable.ic_stat);
        }
        return b.build();
    }

    /** Іконка в рядку стану: зверху значення радіації, знизу стрілка тенденції тиску. */
    private static Bitmap statusIcon(String value, String arrow) {
        int size = 96;
        Bitmap bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        Canvas cv = new Canvas(bmp);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.WHITE);
        paint.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        paint.setTextAlign(Paint.Align.CENTER);

        String top = value.startsWith("0.") ? value.substring(1) : value;
        float ts = 52f;
        paint.setTextSize(ts);
        while (paint.measureText(top) > size - 2 && ts > 20f) {
            ts -= 2f;
            paint.setTextSize(ts);
        }
        cv.drawText(top, size / 2f, 46f, paint);

        paint.setTextSize(46f);
        cv.drawText(arrow == null || arrow.isEmpty() ? "·" : arrow, size / 2f, 92f, paint);
        return bmp;
    }

    private static void showAlert(Context c, int id, String title, String msg) {
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, CHANNEL_ALARM)
                : new Notification.Builder(c);
        Intent open = new Intent(c, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        b.setContentTitle(title)
                .setContentText(msg)
                .setContentIntent(PendingIntent.getActivity(c, id, open, piFlags()))
                .setAutoCancel(true)
                .setColor(Color.RED)
                .setCategory(Notification.CATEGORY_ALARM)
                .setSmallIcon(R.drawable.ic_stat);
        if (Build.VERSION.SDK_INT < 26) {
            b.setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL);
        }
        try {
            ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).notify(id, b.build());
        } catch (SecurityException ignored) {
        }
    }

    static int piFlags() {
        int f = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) f |= PendingIntent.FLAG_IMMUTABLE;
        return f;
    }

    /** Наступне оновлення через вибраний інтервал (5 / 10 / 15 хв). */
    public static void scheduleNext(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        Intent i = new Intent(c, RefreshReceiver.class);
        PendingIntent pi = PendingIntent.getBroadcast(c, 10, i, piFlags());
        long intervalMs = getIntervalMinutes(c) * 60_000L;
        long at = SystemClock.elapsedRealtime() + intervalMs;
        if (Build.VERSION.SDK_INT >= 23) {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        } else {
            am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        }
    }

    public static void cancelSchedule(Context c) {
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        Intent i = new Intent(c, RefreshReceiver.class);
        am.cancel(PendingIntent.getBroadcast(c, 10, i, piFlags()));
    }

    public static void startService(Context c) {
        if (!isEnabled(c)) return;
        Intent i = new Intent(c, MonitorService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
            else c.startService(i);
        } catch (Exception ignored) {
            scheduleNext(c);
        }
    }

    public static void stopAll(Context c) {
        cancelSchedule(c);
        c.stopService(new Intent(c, MonitorService.class));
        ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(NOTIF_ID);
    }
}
