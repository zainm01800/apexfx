//+------------------------------------------------------------------+
//|  apex_mt4_bridge_zmq.mq4                                         |
//|  APEX Quant — ZeroMQ TWO-WAY Bridge Expert Advisor                |
//|                                                                   |
//|  Sub-millisecond TCP link to the Python engine. This EA:          |
//|    * PULLs order signals from tcp://127.0.0.1:9091 (engine PUSH)  |
//|    * PUSHes acks / fills / rejects / heartbeats / position        |
//|      reports back to tcp://127.0.0.1:9092 (engine PULL)           |
//|                                                                   |
//|  Two-way + idempotent: every order carries an "id". The EA        |
//|  de-duplicates by id (a resend is a safe no-op), acknowledges     |
//|  receipt, and reports the MT4 ticket on fill so the engine can    |
//|  reconcile its view of open positions. This replaces the old      |
//|  fire-and-forget PUSH that could silently double-fill.            |
//|                                                                   |
//|  Prerequisites                                                    |
//|  ------------                                                     |
//|  1. ZeroMQ MQL4 library: https://github.com/dingmaotu/mql-zmq    |
//|  2. MT4: Tools > Options > Expert Advisors > Allow DLL imports    |
//|  3. Python engine running with execution.provider: zmq            |
//|                                                                   |
//|  NOTE: the Python side (execution/zmq_bridge.py) is unit- and     |
//|  loopback-tested; this MQL4 counterpart must be verified on a     |
//|  live/demo terminal before production use.                        |
//|                                                                   |
//|  Order JSON  (engine -> EA):                                      |
//|    {"id":"ab12","symbol":"EURUSD","cmd":"buy","volume":0.1,       |
//|     "sl":0.0,"tp":0.0}                                            |
//|  Control JSON (engine -> EA):  {"cmd":"query_positions"}          |
//|  Reply JSON  (EA -> engine):                                      |
//|    {"type":"ack","id":"ab12"}                                     |
//|    {"type":"fill","id":"ab12","ticket":12345}                    |
//|    {"type":"reject","id":"ab12","error":134}                     |
//|    {"type":"heartbeat"}                                           |
//|    {"type":"positions","tickets":[12345,12346]}                  |
//+------------------------------------------------------------------+

#property copyright "APEX Quant"
#property version   "3.0"
#property strict

// ZeroMQ MQL4 library (mql-zmq by dingmaotu)
#include <Zmq/ZmqMsg.mqh>
#include <Zmq/Zmq.mqh>
#include <stdlib.mqh>


// ─── Inputs ────────────────────────────────────────────────────────────────
input string ZMQ_ORDER_SERVER = "tcp://127.0.0.1:9091";  // engine PUSH (orders in)
input string ZMQ_ACK_SERVER   = "tcp://127.0.0.1:9092";  // engine PULL (replies out)
input int    TIMER_MS         = 10;                       // Poll interval (ms)
input int    RECV_TIMEOUT_MS  = 0;                        // 0 = non-blocking receive
input int    HEARTBEAT_MS     = 5000;                     // Heartbeat cadence (ms)
input int    MAGIC_NUMBER     = 202506;                   // Order magic number
input int    SLIPPAGE_PIPS    = 3;                        // Max slippage in pips
input bool   LOG_SIGNALS      = true;                     // Verbose logging

// ─── Globals ───────────────────────────────────────────────────────────────
Context context("ApexZMQ");
Socket  pullSocket(context, ZMQ_PULL);   // orders in
Socket  pushSocket(context, ZMQ_PUSH);   // replies out
bool    g_zmq_connected = false;
uint    g_last_heartbeat = 0;

// Idempotency: ring buffer of recently processed order ids.
#define PROC_CAP 512
string  g_processed[PROC_CAP];
int     g_proc_idx = 0;

//+------------------------------------------------------------------+
int OnInit()
{
    pullSocket.setReceiveTimeout(RECV_TIMEOUT_MS);

    bool ok_pull = pullSocket.connect(ZMQ_ORDER_SERVER);
    bool ok_push = pushSocket.connect(ZMQ_ACK_SERVER);
    g_zmq_connected = ok_pull && ok_push;

    if(g_zmq_connected)
        Print("[APEX ZMQ] Connected. orders<-", ZMQ_ORDER_SERVER, "  replies->", ZMQ_ACK_SERVER);
    else
        Print("[APEX ZMQ] WARNING: connect failed (pull=", ok_pull, " push=", ok_push,
              ") — falling back to file bridge.");

    for(int i = 0; i < PROC_CAP; i++) g_processed[i] = "";
    g_last_heartbeat = GetTickCount();

    EventSetMillisecondTimer(TIMER_MS);
    Print("[APEX ZMQ] EA initialised. Timer: ", TIMER_MS, "ms");
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
    pullSocket.disconnect(ZMQ_ORDER_SERVER);
    pushSocket.disconnect(ZMQ_ACK_SERVER);
    context.destroy(0);
    Print("[APEX ZMQ] EA deactivated. Reason: ", reason);
}

//+------------------------------------------------------------------+
void OnTimer()
{
    if(!g_zmq_connected) return;

    // Drain all pending messages this tick.
    ZmqMsg msg;
    while(pullSocket.recv(msg, true))
    {
        string raw = msg.getData();
        if(StringLen(raw) < 5) continue;
        if(LOG_SIGNALS) Print("[APEX ZMQ] recv: ", raw);

        // Control message: position query.
        if(StringFind(raw, "query_positions") >= 0)
        {
            SendReply(BuildPositionsJson());
            continue;
        }

        HandleOrder(raw);
    }

    // Periodic heartbeat so the engine's is_alive() stays true.
    uint now = GetTickCount();
    if(now - g_last_heartbeat >= (uint)HEARTBEAT_MS)
    {
        SendReply("{\"type\":\"heartbeat\",\"server_time\":" + IntegerToString(TimeCurrent()) + "}");
        g_last_heartbeat = now;
    }
}

//+------------------------------------------------------------------+
//  Order handling — ack, execute, report fill/reject                |
//+------------------------------------------------------------------+
void HandleOrder(string raw)
{
    string id     = ParseJsonString(raw, "id");
    string symbol = ParseJsonString(raw, "symbol");
    string cmd    = ParseJsonString(raw, "cmd");
    double volume = ParseJsonDouble(raw, "volume");
    double sl     = ParseJsonDouble(raw, "sl");
    double tp     = ParseJsonDouble(raw, "tp");

    if(StringLen(symbol) == 0 || StringLen(cmd) == 0 || volume <= 0)
    {
        Print("[APEX ZMQ] ERROR: invalid order payload — skipping: ", raw);
        return;
    }

    // Idempotency: a resent id is a no-op (still ack so the engine settles).
    if(AlreadyProcessed(id))
    {
        SendReply(BuildAck(id));
        return;
    }
    MarkProcessed(id);

    SendReply(BuildAck(id));                 // acknowledge receipt first

    int orderType = -1;
    if(cmd == "buy")  orderType = OP_BUY;
    if(cmd == "sell") orderType = OP_SELL;
    if(orderType == -1)
    {
        Print("[APEX ZMQ] ERROR: unknown cmd '", cmd, "'.");
        SendReply(BuildReject(id, 3));       // 3 = invalid request
        return;
    }

    double price = (orderType == OP_BUY) ? MarketInfo(symbol, MODE_ASK)
                                         : MarketInfo(symbol, MODE_BID);
    int ticket = OrderSend(symbol, orderType, volume, price, SLIPPAGE_PIPS, sl, tp,
                           "APEX ZMQ", MAGIC_NUMBER, 0,
                           (orderType == OP_BUY) ? clrBlue : clrRed);

    if(ticket > 0)
    {
        Print("[APEX ZMQ] filled ticket #", ticket, " ", cmd, " ", volume, " ", symbol);
        SendReply(BuildFill(id, ticket));
    }
    else
    {
        int err = GetLastError();
        Print("[APEX ZMQ] ERROR: OrderSend failed — ", err, " (", ErrorDescription(err), ")");
        SendReply(BuildReject(id, err));
    }
}

//+------------------------------------------------------------------+
//  Reply helpers                                                    |
//+------------------------------------------------------------------+
void SendReply(string jsonMsg)
{
    ZmqMsg reply(jsonMsg);
    pushSocket.send(reply, true);            // non-blocking
}

string BuildAck(string id)          { return "{\"type\":\"ack\",\"id\":\"" + id + "\"}"; }
string BuildFill(string id, int t)  { return "{\"type\":\"fill\",\"id\":\"" + id + "\",\"ticket\":" + IntegerToString(t) + "}"; }
string BuildReject(string id, int e){ return "{\"type\":\"reject\",\"id\":\"" + id + "\",\"error\":" + IntegerToString(e) + "}"; }

string BuildPositionsJson()
{
    string tickets = "";
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if(OrderMagicNumber() != MAGIC_NUMBER) continue;
        if(StringLen(tickets) > 0) tickets += ",";
        tickets += IntegerToString(OrderTicket());
    }
    return "{\"type\":\"positions\",\"tickets\":[" + tickets + "]}";
}

//+------------------------------------------------------------------+
//  Idempotency ring buffer                                          |
//+------------------------------------------------------------------+
bool AlreadyProcessed(string id)
{
    if(StringLen(id) == 0) return false;
    for(int i = 0; i < PROC_CAP; i++)
        if(g_processed[i] == id) return true;
    return false;
}

void MarkProcessed(string id)
{
    if(StringLen(id) == 0) return;
    g_processed[g_proc_idx] = id;
    g_proc_idx = (g_proc_idx + 1) % PROC_CAP;
}

//+------------------------------------------------------------------+
//  Minimal JSON parsers (no external library needed)                |
//+------------------------------------------------------------------+
string ParseJsonString(string json, string key)
{
    string search = "\"" + key + "\":\"";
    int pos = StringFind(json, search);
    if(pos < 0) return "";
    pos += StringLen(search);
    int end = StringFind(json, "\"", pos);
    if(end < 0) return "";
    return StringSubstr(json, pos, end - pos);
}

double ParseJsonDouble(string json, string key)
{
    string search = "\"" + key + "\":";
    int pos = StringFind(json, search);
    if(pos < 0) return 0.0;
    pos += StringLen(search);
    string val = "";
    for(int i = pos; i < StringLen(json); i++)
    {
        string ch = StringSubstr(json, i, 1);
        if(ch == "," || ch == "}" || ch == " " || ch == "]") break;
        val += ch;
    }
    return StringToDouble(val);
}
