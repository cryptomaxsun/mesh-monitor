package com.maksim8.meshmonitor;

import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/** Foreground-служба, що тримає постійне сповіщення з радіацією та барометром. */
public class MonitorService extends Service {

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NotifyHelper.NOTIF_ID, NotifyHelper.buildNotification(this),
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } else {
                startForeground(NotifyHelper.NOTIF_ID, NotifyHelper.buildNotification(this));
            }
        } catch (Exception ignored) {
        }
        if (!NotifyHelper.isEnabled(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                NotifyHelper.refreshNow(MonitorService.this);
                NotifyHelper.scheduleNext(MonitorService.this);
            }
        }).start();
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
