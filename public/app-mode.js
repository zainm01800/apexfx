(function() {
  // Apply saved layout mode immediately to avoid layout shifts (FOUC)
  const savedLayout = localStorage.getItem('apex_layout') || 'web';
  if (savedLayout === 'app' && window.innerWidth >= 768) {
    document.documentElement.classList.add('layout-app');
  }
})();

// Shared settings functions
window.openApexSettings = function() {
  injectSettingsModal();
  const modal = document.getElementById('apexSettingsModalBg');
  if (modal) {
    // Populate form
    const layoutToggle = document.getElementById('apexLayoutToggle');
    const localLlmToggle = document.getElementById('apexLocalLlmToggle');
    const baseUrlInput = document.getElementById('apexLocalBaseUrl');
    const modelInput = document.getElementById('apexLocalModel');
    const apiKeyInput = document.getElementById('apexLocalApiKey');
    
    layoutToggle.checked = localStorage.getItem('apex_layout') === 'app';
    const localEnabled = localStorage.getItem('apex_local_llm_enabled') === 'true';
    localLlmToggle.checked = localEnabled;
    
    baseUrlInput.value = localStorage.getItem('apex_local_llm_base_url') || 'http://localhost:11434/v1';
    modelInput.value = localStorage.getItem('apex_local_llm_model') || 'llama3';
    apiKeyInput.value = localStorage.getItem('apex_local_llm_api_key') || '';
    
    // Toggle visibility of fields
    const fields = document.getElementById('apexLocalLlmFields');
    fields.style.display = localEnabled ? 'flex' : 'none';
    
    // Clear test status
    document.getElementById('apexLlmTestStatus').innerHTML = '';
    
    modal.classList.add('open');
  }
};

window.closeApexSettings = function(event) {
  const modal = document.getElementById('apexSettingsModalBg');
  if (modal) {
    modal.classList.remove('open');
  }
};

window.toggleApexLayout = function(checked) {
  // Option change visual feedback
};

window.toggleLocalLlm = function(checked) {
  const fields = document.getElementById('apexLocalLlmFields');
  fields.style.display = checked ? 'flex' : 'none';
};

window.saveApexSettings = function() {
  const layoutChecked = document.getElementById('apexLayoutToggle').checked;
  const localLlmChecked = document.getElementById('apexLocalLlmToggle').checked;
  const baseUrl = document.getElementById('apexLocalBaseUrl').value.trim();
  const model = document.getElementById('apexLocalModel').value.trim();
  const apiKey = document.getElementById('apexLocalApiKey').value.trim();
  
  localStorage.setItem('apex_layout', layoutChecked ? 'app' : 'web');
  localStorage.setItem('apex_local_llm_enabled', localLlmChecked ? 'true' : 'false');
  if (baseUrl) localStorage.setItem('apex_local_llm_base_url', baseUrl);
  if (model) localStorage.setItem('apex_local_llm_model', model);
  localStorage.setItem('apex_local_llm_api_key', apiKey);
  
  if (layoutChecked) {
    document.documentElement.classList.add('layout-app');
    document.body.classList.add('layout-app');
  } else {
    document.documentElement.classList.remove('layout-app');
    document.body.classList.remove('layout-app');
  }
  
  closeApexSettings();
  window.location.reload();
};

window.testLocalLlmConnection = async function() {
  const statusDiv = document.getElementById('apexLlmTestStatus');
  statusDiv.className = 'apex-status-badge loading';
  statusDiv.textContent = 'Testing connection...';
  
  const rawUrl = document.getElementById('apexLocalBaseUrl').value.trim() || 'http://localhost:11434/v1';
  const model = document.getElementById('apexLocalModel').value.trim() || 'llama3';
  const apiKey = document.getElementById('apexLocalApiKey').value.trim();
  
  // Clean up URL
  let baseUrl = rawUrl;
  if (baseUrl.endsWith('/chat/completions')) {
    baseUrl = baseUrl.substring(0, baseUrl.length - 17);
  }
  if (baseUrl.endsWith('/')) {
    baseUrl = baseUrl.substring(0, baseUrl.length - 1);
  }
  
  const isLocalhost = baseUrl.includes('localhost') || baseUrl.includes('127.0.0.1');
  
  try {
    let reply = '';
    
    if (isLocalhost) {
      const headers = { 'Content-Type': 'application/json' };
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
      
      const res = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model: model,
          messages: [
            { role: 'system', content: 'Connection check. Answer only with OK.' },
            { role: 'user', content: 'hello' }
          ],
          max_tokens: 5,
          temperature: 0.1
        })
      });
      
      if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
      const data = await res.json();
      reply = data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content 
        ? data.choices[0].message.content.trim() 
        : 'No response text';
    } else {
      // Route through Vercel backend proxy to bypass browser CORS constraints
      const res = await fetch('/api/ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          useLocalLlm: true,
          localLlmUrl: `${baseUrl}/chat/completions`,
          localLlmModel: model,
          localLlmKey: apiKey,
          system: 'Connection check. Answer only with OK.',
          prompt: 'hello',
          max_tokens: 5,
          temperature: 0.1
        })
      });
      
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP Error ${res.status}`);
      }
      
      const data = await res.json();
      reply = data.text ? data.text.trim() : 'No response text';
    }
      
    statusDiv.className = 'apex-status-badge success';
    statusDiv.textContent = `Connected successfully! (Model: ${model}, Reply: "${reply}")`;
  } catch (err) {
    statusDiv.className = 'apex-status-badge error';
    statusDiv.textContent = `Connection failed: ${err.message}. Make sure the URL is correct, the service is online, and API key is valid.`;
  }
};

window.callLocalLLM = async function(system, prompt, maxTokens) {
  const rawUrl = localStorage.getItem('apex_local_llm_base_url') || 'http://localhost:11434/v1';
  const model = localStorage.getItem('apex_local_llm_model') || 'llama3';
  const apiKey = localStorage.getItem('apex_local_llm_api_key') || '';
  
  let baseUrl = rawUrl;
  if (baseUrl.endsWith('/chat/completions')) {
    baseUrl = baseUrl.substring(0, baseUrl.length - 17);
  }
  if (baseUrl.endsWith('/')) {
    baseUrl = baseUrl.substring(0, baseUrl.length - 1);
  }
  
  const isLocalhost = baseUrl.includes('localhost') || baseUrl.includes('127.0.0.1');
  
  if (isLocalhost) {
    const headers = { 'Content-Type': 'application/json' };
    if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
    
    const res = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model: model,
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: prompt }
        ],
        temperature: 0.3,
        max_tokens: maxTokens
      })
    });
    
    if (!res.ok) {
      throw new Error(`Local LLM API error: HTTP ${res.status}`);
    }
    
    const data = await res.json();
    if (!data.choices || !data.choices[0] || !data.choices[0].message) {
      throw new Error('Invalid response format from Local LLM API');
    }
    return data.choices[0].message.content || '';
  } else {
    // Route cloud external URLs through our backend proxy to bypass browser CORS block
    const res = await fetch('/api/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        useLocalLlm: true,
        localLlmUrl: `${baseUrl}/chat/completions`,
        localLlmModel: model,
        localLlmKey: apiKey,
        system: system,
        prompt: prompt,
        max_tokens: maxTokens,
        temperature: 0.3
      })
    });
    
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || `Proxy AI error: HTTP ${res.status}`);
    }
    
    const data = await res.json();
    return data.text || '';
  }
};

function injectSettingsModal() {
  if (document.getElementById('apexSettingsModalBg')) return;
  
  const modalHtml = `
    <div class="apex-modal-bg" id="apexSettingsModalBg" onclick="closeApexSettings(event)">
      <div class="apex-modal" onclick="event.stopPropagation()">
        <div class="apex-modal-header">
          <div class="apex-modal-title">APEX Settings</div>
          <button class="apex-modal-close" onclick="closeApexSettings()">&times;</button>
        </div>
        <div class="apex-modal-body">
          <!-- Layout Section -->
          <div class="apex-settings-group">
            <div class="apex-settings-label">Interface Layout</div>
            <div class="apex-settings-row">
              <div>
                <div style="font-size: 13px; font-weight: 500;">Desktop App Mode</div>
                <div class="apex-settings-desc">Transforms the top header into a macOS style side navigation menu.</div>
              </div>
              <label class="apex-toggle-wrap">
                <input type="checkbox" id="apexLayoutToggle" class="apex-toggle-input">
                <span class="apex-toggle-slider"></span>
              </label>
            </div>
          </div>
          
          <hr style="border: none; border-top: 1px solid var(--border); margin: 6px 0;">
          
          <!-- Local LLM Section -->
          <div class="apex-settings-group">
            <div class="apex-settings-label">Local LLM Configuration</div>
            <div class="apex-settings-desc" style="margin-bottom: 8px;">
              Bypass cloud APIs and direct agent scans to a model running locally on your machine (e.g. Ollama, LM Studio).
            </div>
            
            <div class="apex-settings-row">
              <div>
                <div style="font-size: 13px; font-weight: 500;">Enable Local LLM</div>
                <div class="apex-settings-desc">Run inference locally for research and re-checks.</div>
              </div>
              <label class="apex-toggle-wrap">
                <input type="checkbox" id="apexLocalLlmToggle" class="apex-toggle-input" onchange="toggleLocalLlm(this.checked)">
                <span class="apex-toggle-slider"></span>
              </label>
            </div>
            
            <div id="apexLocalLlmFields" style="display: none; flex-direction: column; gap: 12px; margin-top: 8px;">
              <div>
                <label class="apex-settings-desc" style="display: block; margin-bottom: 4px;">Local API Base URL</label>
                <input type="text" id="apexLocalBaseUrl" class="apex-input" placeholder="e.g. http://localhost:11434/v1">
              </div>
              <div>
                <label class="apex-settings-desc" style="display: block; margin-bottom: 4px;">Model Name</label>
                <input type="text" id="apexLocalModel" class="apex-input" placeholder="e.g. llama3, deepseek-coder">
              </div>
              <div>
                <label class="apex-settings-desc" style="display: block; margin-bottom: 4px;">API Key (Optional)</label>
                <input type="password" id="apexLocalApiKey" class="apex-input" placeholder="Optional key if required">
              </div>
              
              <div style="margin-top: 4px;">
                <button class="apex-btn-secondary" onclick="testLocalLlmConnection()">Test Connection</button>
                <div id="apexLlmTestStatus" style="margin-top: 6px;"></div>
              </div>
            </div>
          </div>
        </div>
        <div class="apex-modal-footer">
          <button class="apex-btn-secondary" onclick="closeApexSettings()">Cancel</button>
          <button class="apex-btn-primary" onclick="saveApexSettings()">Save Changes</button>
        </div>
      </div>
    </div>
  `;
  const wrapper = document.createElement('div');
  wrapper.innerHTML = modalHtml;
  document.body.appendChild(wrapper.firstElementChild);
}

// Dom content load handler
window.addEventListener('DOMContentLoaded', () => {
  const savedLayout = localStorage.getItem('apex_layout') || 'web';
  if (savedLayout === 'app' && window.innerWidth >= 768) {
    document.body.classList.add('layout-app');
  }
  
  // Inject Settings button into header
  const header = document.querySelector('.header');
  if (header) {
    const right = header.querySelector('.header-right');
    if (right) {
      const btn = document.createElement('button');
      btn.className = 'nav-settings-btn';
      btn.id = 'navSettingsBtn';
      btn.textContent = 'Settings';
      btn.onclick = openApexSettings;
      right.insertBefore(btn, right.firstChild);
    } else {
      const nav = header.querySelector('.nav');
      if (nav) {
        const btn = document.createElement('button');
        btn.className = 'nav-settings-btn';
        btn.id = 'navSettingsBtn';
        btn.textContent = 'Settings';
        btn.onclick = openApexSettings;
        nav.parentNode.insertBefore(btn, nav.nextSibling);
      }
    }
  }

  // Preload sub-pages and assets to make tab switching instantaneous (0ms network lag)
  const resources = [
    'index.html', 'dashboard.html', 'history.html', 'backtest.html', 'track-record.html', 'how-it-works.html',
    'dashboard.css', 'history.css', 'backtest.css',
    'dashboard.js', 'history.js', 'backtest.js', 'track-record.js'
  ];
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  resources.forEach(res => {
    if (res !== currentPage) {
      const link = document.createElement('link');
      link.rel = 'prefetch';
      link.href = './' + res;
      document.head.appendChild(link);
    }
  });
});
