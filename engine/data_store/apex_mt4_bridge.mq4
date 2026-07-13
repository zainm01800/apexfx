//+------------------------------------------------------------------+
//|                                             apex_mt4_bridge.mq4  |
//|                                  Copyright 2026, APEX FX Quant   |
//|                                        https://apexfx.vercel.app |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, APEX FX Quant"
#property link      "https://apexfx.vercel.app"
#property version   "1.00"
#property strict

// Input Parameters
input string   SignalFileName = "mt4_signals.json"; // Signal file in shared terminal folder
input int      SlippagePips   = 3;                   // Allowed slippage in pips
input int      MagicNumber    = 88888;               // Unique ID for APEX trades
input int      ScanIntervalMs = 500;                 // Scan speed in milliseconds

// Global Timer
int TimerId;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("[APEX Bridge] Initializing MT4 Signal Bridge...");
   EventSetMillisecondTimer(ScanIntervalMs);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("[APEX Bridge] Deinitialized.");
}

//+------------------------------------------------------------------+
//| Timer event function (scans for new JSON signals)                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   WriteLiveTrades();
   WriteClosedHistory();
   WriteAccountInfo();

   // Check if file exists in the shared folder
   if(!FileIsExist(SignalFileName, FILE_COMMON))
   {
      return; // No pending signals
   }

   // Open and read file
   int handle = FileOpen(SignalFileName, FILE_READ|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      Print("[APEX Bridge] Error: Found signal file but could not open it.");
      return;
   }

   string content = "";
   while(!FileIsEnding(handle))
   {
      content += FileReadString(handle);
   }
   FileClose(handle);

   // Instantly delete the file to avoid double-execution
   FileDelete(SignalFileName, FILE_COMMON);

   Print("[APEX Bridge] Received new signal: ", content);

   // Parse fields manually from simple JSON payload
   string symbol   = ParseJsonString(content, "symbol");
   string cmd      = ParseJsonString(content, "cmd");
   double volume   = ParseJsonDouble(content, "volume");
   double sl       = ParseJsonDouble(content, "sl");
   double tp       = ParseJsonDouble(content, "tp");

   if(symbol == "" || cmd == "" || volume <= 0.0)
   {
      Print("[APEX Bridge] Error: Failed to parse execution parameters.");
      return;
   }

   // Standardise Symbol Name (handle broker suffix like EURUSDm or EURUSD.ecn)
   string matchedSymbol = MatchSymbol(symbol);
   if(matchedSymbol == "")
   {
      Print("[APEX Bridge] Error: Symbol '", symbol, "' is not available in MT4 Market Watch.");
      return;
   }

   // Execute order
   ExecuteOrder(matchedSymbol, cmd, volume, sl, tp);
}

//+------------------------------------------------------------------+
//| Match symbol suffix (e.g. EURUSD -> EURUSD.m)                     |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Clean symbol name by removing non-alphanumeric characters       |
//+------------------------------------------------------------------+
string CleanSymbolName(string text)
{
   string result = "";
   for(int j = 0; j < StringLen(text); j++)
     {
      ushort charCode = StringGetCharacter(text, j);
      if((charCode >= 65 && charCode <= 90) || 
         (charCode >= 97 && charCode <= 122) || 
         (charCode >= 48 && charCode <= 57))
        {
         string ch = " ";
         StringSetCharacter(ch, 0, charCode);
         result += ch;
        }
     }
   string upperRes = result;
   StringToUpper(upperRes);
   return upperRes;
}

string MatchSymbol(string baseSymbol)
{
   string cleanBase = CleanSymbolName(baseSymbol);
   for(int k = 0; k < SymbolsTotal(false); k++)
   {
      string sym = SymbolName(k, false);
      string cleanSym = CleanSymbolName(sym);
      if(StringFind(cleanSym, cleanBase) >= 0)
      {
         return sym;
      }
   }
   return "";
}

//+------------------------------------------------------------------+
//| Execute Order on Broker Terminal                                  |
//+------------------------------------------------------------------+
void ExecuteOrder(string sym, string cmd, double vol, double sl, double tp)
{
   int type = -1;
   double price = 0.0;
   color arrowColor = clrNONE;

   if(cmd == "buy")
   {
      type = OP_BUY;
      price = SymbolInfoDouble(sym, SYMBOL_ASK);
      arrowColor = clrGreen;
   }
   else if(cmd == "sell")
   {
      type = OP_SELL;
      price = SymbolInfoDouble(sym, SYMBOL_BID);
      arrowColor = clrRed;
   }
   else
   {
      Print("[APEX Bridge] Invalid order command: ", cmd);
      return;
   }

   RefreshRates();
   
   Print("[APEX Bridge] Sending order: ", cmd, " ", vol, " lots of ", sym, " at ", price, " SL:", sl, " TP:", tp);

   int ticket = OrderSend(sym, type, vol, price, SlippagePips, sl, tp, "APEX Quant Auto-Trade", MagicNumber, 0, arrowColor);
   
   if(ticket > 0)
   {
      Print("[APEX Bridge] Success! Order #", ticket, " executed on ", sym);
   }
   else
   {
      int error = GetLastError();
      Print("[APEX Bridge] Order failed on ", sym, ". Error code: ", error, " - ", ErrorDescription(error));
   }
}

//+------------------------------------------------------------------+
//| Simple JSON String Extractor                                      |
//+------------------------------------------------------------------+
string ParseJsonString(string json, string key)
{
   string searchKey = "\"" + key + "\"";
   int startIdx = StringFind(json, searchKey);
   if(startIdx < 0) return "";

   int colonIdx = StringFind(json, ":", startIdx);
   if(colonIdx < 0) return "";

   int firstQuote = StringFind(json, "\"", colonIdx);
   if(firstQuote < 0) return "";

   int secondQuote = StringFind(json, "\"", firstQuote + 1);
   if(secondQuote < 0) return "";

   return StringSubstr(json, firstQuote + 1, secondQuote - firstQuote - 1);
}

//+------------------------------------------------------------------+
//| Simple JSON Double Extractor                                      |
//+------------------------------------------------------------------+
double ParseJsonDouble(string json, string key)
{
   string searchKey = "\"" + key + "\"";
   int startIdx = StringFind(json, searchKey);
   if(startIdx < 0) return 0.0;

   int colonIdx = StringFind(json, ":", startIdx);
   if(colonIdx < 0) return 0.0;

   // Read until comma or closing brace
   int commaIdx = StringFind(json, ",", colonIdx);
   int braceIdx = StringFind(json, "}", colonIdx);
   int endIdx = (commaIdx < braceIdx && commaIdx > 0) ? commaIdx : braceIdx;
   if(endIdx < 0) return 0.0;

   string valStr = StringSubstr(json, colonIdx + 1, endIdx - colonIdx - 1);
   valStr = StringTrim(valStr);
   
   // Clean extra quotes if type is string in JSON
   valStr = ApexStringReplace(valStr, "\"", "");
   
   return StringToDouble(valStr);
}

//+------------------------------------------------------------------+
//| Helper to trim whitespace                                         |
//+------------------------------------------------------------------+
string StringTrim(string text)
{
   int start = 0;
   int end = StringLen(text) - 1;
   while(start <= end && (StringGetCharacter(text, start) == ' ' || StringGetCharacter(text, start) == '\t' || StringGetCharacter(text, start) == '\n' || StringGetCharacter(text, start) == '\r')) start++;
   while(end >= start && (StringGetCharacter(text, end) == ' ' || StringGetCharacter(text, end) == '\t' || StringGetCharacter(text, end) == '\n' || StringGetCharacter(text, end) == '\r')) end--;
   return StringSubstr(text, start, end - start + 1);
}

//+------------------------------------------------------------------+
//| Helper to replace characters                                      |
//+------------------------------------------------------------------+
string ApexStringReplace(string text, string target, string replacement)
{
   string result = text;
   int idx = StringFind(result, target);
   while(idx >= 0)
   {
      result = StringSubstr(result, 0, idx) + replacement + StringSubstr(result, idx + StringLen(target));
      idx = StringFind(result, target, idx + StringLen(replacement));
   }
   return result;
}

//+------------------------------------------------------------------+
//| MQL4 standard error descriptions                                  |
//+------------------------------------------------------------------+
string ErrorDescription(int errorCode)
{
   switch(errorCode)
   {
      case 1:   return "No error, but progress is unclear";
      case 2:   return "Common error";
      case 3:   return "Invalid trade parameters";
      case 4:   return "Trade server is busy";
      case 129: return "Invalid price";
      case 130: return "Invalid stops (SL or TP too close)";
      case 131: return "Invalid trade volume (lot size wrong)";
      case 132: return "Market is closed";
      case 133: return "Trade is disabled";
      case 134: return "Not enough money to open trade";
      case 135: return "Price changed";
      case 138: return "Requote";
      case 146: return "Trade context is busy";
      default:  return "Unknown error code";
   }
}

//+------------------------------------------------------------------+
//| Write live open positions to a shared common JSON file           |
//+------------------------------------------------------------------+
void WriteLiveTrades()
{
   string filename = "mt4_positions.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   string content = "[";
   bool first = true;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         // Exclude balance deposits/withdrawals and pending orders (OP_BUY=0, OP_SELL=1)
         if(OrderType() > 1) continue; 
         
         if(!first) content += ",";
         first = false;
         content += "{\"ticket\":" + IntegerToString(OrderTicket()) +
                    ",\"symbol\":\"" + OrderSymbol() + "\"" +
                    ",\"cmd\":" + IntegerToString(OrderType()) +
                    ",\"volume\":" + DoubleToString(OrderLots(), 2) +
                    ",\"open_price\":" + DoubleToString(OrderOpenPrice(), 5) +
                    ",\"sl\":" + DoubleToString(OrderStopLoss(), 5) +
                    ",\"tp\":" + DoubleToString(OrderTakeProfit(), 5) +
                    ",\"close_price\":" + DoubleToString(MarketInfo(OrderSymbol(), MODE_BID), 5) +
                    ",\"profit\":" + DoubleToString(OrderProfit() + OrderSwap() + OrderCommission(), 2) +
                    ",\"magic\":" + IntegerToString(OrderMagicNumber()) +
                    ",\"open_time\":" + IntegerToString(OrderOpenTime()) + "}";
      }
   }
   content += "]";
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Write closed trade history to a shared common JSON file          |
//+------------------------------------------------------------------+
void WriteClosedHistory()
{
   string filename = "mt4_history.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   string content = "[";
   bool first = true;
   int count = 0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0 && count < 50; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
      {
         // Exclude balance transactions (OrderType > OP_SELL)
         if(OrderType() > 1) continue; 
         
         if(!first) content += ",";
         first = false;
         count++;
         
         content += "{\"ticket\":" + IntegerToString(OrderTicket()) +
                    ",\"symbol\":\"" + OrderSymbol() + "\"" +
                    ",\"cmd\":" + IntegerToString(OrderType()) +
                    ",\"volume\":" + DoubleToString(OrderLots(), 2) +
                    ",\"open_price\":" + DoubleToString(OrderOpenPrice(), 5) +
                    ",\"sl\":" + DoubleToString(OrderStopLoss(), 5) +
                    ",\"tp\":" + DoubleToString(OrderTakeProfit(), 5) +
                    ",\"close_price\":" + DoubleToString(OrderClosePrice(), 5) +
                    ",\"profit\":" + DoubleToString(OrderProfit() + OrderSwap() + OrderCommission(), 2) +
                    ",\"magic\":" + IntegerToString(OrderMagicNumber()) +
                    ",\"open_time\":" + IntegerToString(OrderOpenTime()) + 
                    ",\"close_time\":" + IntegerToString(OrderCloseTime()) + "}";
      }
   }
   content += "]";
   FileWriteString(handle, content);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Write live account statistics to a shared common JSON file       |
//+------------------------------------------------------------------+
void WriteAccountInfo()
{
   string filename = "mt4_account.json";
   int handle = FileOpen(filename, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE) return;

   double startBalance = 0.0;
   for(int i = 0; i < OrdersHistoryTotal(); i++)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
      {
         if(OrderType() == 6) // OP_BALANCE (deposit/withdrawal)
         {
            double p = OrderProfit();
            if(p > 0.0) // only positive deposits as starting base
            {
               startBalance += p;
            }
         }
      }
   }
   if(startBalance <= 0.0) {
      // Fallback: current balance minus floating profit if history lacks deposits
      startBalance = AccountBalance() - AccountProfit();
   }

   string content = "{\"balance\":" + DoubleToString(AccountBalance(), 2) +
                    ",\"equity\":" + DoubleToString(AccountEquity(), 2) +
                    ",\"profit\":" + DoubleToString(AccountProfit(), 2) +
                    ",\"free_margin\":" + DoubleToString(AccountFreeMargin(), 2) +
                    ",\"leverage\":" + IntegerToString(AccountLeverage()) +
                    ",\"currency\":\"" + AccountCurrency() + "\"" +
                    ",\"name\":\"" + AccountName() + "\"" +
                    ",\"company\":\"" + AccountCompany() + "\"" +
                    ",\"start_balance\":" + DoubleToString(startBalance, 2) + "}";
                    
   FileWriteString(handle, content);
   FileClose(handle);
}
