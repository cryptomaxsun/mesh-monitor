package com.maksim8.meshmonitor;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Після перезавантаження телефона чи оновлення додатка знову запускає сповіщення. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        Context app = context.getApplicationContext();
        if (!NotifyHelper.isEnabled(app)) return;
        NotifyHelper.startService(app);
        NotifyHelper.scheduleNext(app);
    }
}
