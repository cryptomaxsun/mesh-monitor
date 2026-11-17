package com.maksim8.meshmonitor;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.widget.RemoteViews;
import android.app.PendingIntent;

/** Віджет на головний екран Android: радіація, барометр і атмосферний тиск. */
public class MeshWidgetProvider extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        for (int id : appWidgetIds) {
            updateOne(context, appWidgetManager, id);
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                NotifyHelper.refreshNow(context.getApplicationContext());
            }
        }).start();
    }

    public static void updateAll(Context context) {
        try {
            AppWidgetManager mgr = AppWidgetManager.getInstance(context);
            ComponentName cn = new ComponentName(context, MeshWidgetProvider.class);
            int[] ids = mgr.getAppWidgetIds(cn);
            if (ids != null) {
                for (int id : ids) {
                    updateOne(context, mgr, id);
                }
            }
        } catch (Exception ignored) {
        }
    }

    private static void updateOne(Context c, AppWidgetManager mgr, int id) {
        SharedPreferences p = NotifyHelper.prefs(c);
        RemoteViews rv = new RemoteViews(c.getPackageName(), R.layout.widget_mesh);
        int alpha = NotifyHelper.getWidgetAlpha(c);
        int bgRes = R.drawable.widget_bg_70;
        if (alpha == 90) bgRes = R.drawable.widget_bg_90;
        else if (alpha == 50) bgRes = R.drawable.widget_bg_50;
        else if (alpha == 30) bgRes = R.drawable.widget_bg_30;
        else if (alpha == 0) bgRes = R.drawable.widget_bg_0;
        rv.setInt(R.id.widget_root, "setBackgroundResource", bgRes);

        rv.setTextViewText(R.id.widget_rad, p.getString(NotifyHelper.KEY_WIDGET_RAD, "☢️ Очікування…"));
        rv.setTextViewText(R.id.widget_baro, p.getString(NotifyHelper.KEY_WIDGET_BARO, "🧭 Барометр: —"));
        rv.setTextViewText(R.id.widget_temp, p.getString(NotifyHelper.KEY_WIDGET_TEMP, "— hPa"));
        rv.setTextViewText(R.id.widget_time, p.getString(NotifyHelper.KEY_WIDGET_TIME, "—"));

        Intent open = new Intent(c, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        rv.setOnClickPendingIntent(R.id.widget_root, PendingIntent.getActivity(c, 20, open, NotifyHelper.piFlags()));

        Intent refresh = new Intent(c, RefreshReceiver.class);
        rv.setOnClickPendingIntent(R.id.widget_refresh, PendingIntent.getBroadcast(c, 21, refresh, NotifyHelper.piFlags()));

        mgr.updateAppWidget(id, rv);
    }
}
