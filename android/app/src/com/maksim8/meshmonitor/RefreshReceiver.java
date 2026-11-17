package com.maksim8.meshmonitor;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.PowerManager;

/** Будильник кожні ~5 хв: завантажує свіжі дані та оновлює сповіщення. */
public class RefreshReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(final Context context, Intent intent) {
        final Context app = context.getApplicationContext();
        final PendingResult result = goAsync();
        PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
        final PowerManager.WakeLock wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MeshMonitor:refresh");
        wl.acquire(25000L);
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    NotifyHelper.refreshNow(app);
                } finally {
                    if (NotifyHelper.isEnabled(app)) {
                        NotifyHelper.scheduleNext(app);
                    }
                    if (wl.isHeld()) wl.release();
                    result.finish();
                }
            }
        }).start();
    }
}
