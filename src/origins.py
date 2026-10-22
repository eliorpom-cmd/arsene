"""Most common surnames and first names of the origins most represented in French
administrative files. A surrogate keeps the origin of the real name ("Benali" -> another
Maghrebi surname) so the model still reasons right on residence permits, nationality or
civil status files; once an origin's list is used up, the general pool takes over.

Lists are for detection AND surrogates: a name listed here is recognised as of that origin.
Keep names that are specific to one origin; a universal first name ("Sarah", "Adam")
would give an origin to anyone."""

ORIGINS = {
    "maghreb": {
        "sur": """Benali Haddad Mansouri Belkacem Brahimi Bouzid Saidi Hamdi Cherif Amrani Benaissa
        Belhadj Bouaziz Kaci Meziane Chaoui Rahmani Khelifi Boukhari Ouali Zerrouki Benyoucef
        Djebbar Hamidi Slimani Aouadi Guerfi Benchikh Laaroussi Tahiri Idrissi Alaoui Bennani
        Berrada Chraibi Lahlou Tazi Ouazzani Bensaid Ghazi Jaziri Trabelsi Ayari Jebali Gharbi
        Dridi Mejri Belaid Boudjema Hadjadj""",
        "F": """Fatima Khadija Yasmina Nadia Samira Karima Leila Naima Amina Malika Soraya Rachida
        Zohra Farida Houria Hanane Siham Imane Salima Djamila Loubna Assia Wafa Nawel Kenza
        Rania  Amel Hafsa Dounia""",
        "M": """Karim Rachid Farid Nabil Samir Mourad Djamel Hakim Khaled Mustapha Abdelkader
        Youssef Hamza Bilal Walid Sofiane Nassim Yacine Kamel Rayan Amine Ilyes Tarek Hicham
        Omar Hassan Abdallah Said Brahim Lotfi Mohamed Mohammed Ahmed Ali Mehdi
        Mounir Nordine Abdel Redouane Abderrahmane Ayoub Anis Zakaria Idir Hocine""",
    },
    "portugal": {
        "sur": """Silva Santos Ferreira Pereira Oliveira Costa Rodrigues Martins Fernandes Gonçalves
        Gomes Lopes Marques Alves Almeida Ribeiro Pinto Carvalho Teixeira Moreira Correia
        Mendes Nunes Soares Vieira Monteiro Cardoso Rocha Neves Coelho Cruz Cunha Pires Ramos
        Reis Simoes Antunes Matos Fonseca Machado Araujo Barbosa Tavares Azevedo Sousa""",
        "F": """Joana Filipa Ines Beatriz Catarina Mafalda Leonor Rita  Carla Fernanda Graça
        Conceição Rosario Fatima Lurdes Teresa   Susana""",
        "M": """Joao Antonio Manuel Jose Francisco Joaquim Rui Nuno Tiago Duarte Goncalo Diogo
        Rodrigo Afonso Vasco Sergio Paulo Fernando Armando Arlindo""",
    },
    "espagne": {
        "sur": """Garcia Fernandez Gonzalez Rodriguez Lopez Martinez Sanchez Perez Gomez 
        Jimenez Ruiz Hernandez Diaz Moreno Alvarez Romero Alonso Gutierrez Navarro Torres
        Dominguez Vazquez Ramos Gil Serrano Blanco Molina Morales Suarez Ortega Delgado
        Castro Ortiz Rubio  Sanz Iglesias Nuñez Medina Garrido Cortes Castillo""",
        "F": """Carmen Pilar Dolores Mercedes Rosario Concepcion Encarnacion Lucia Paloma Montserrat
        Nuria Rocio Inmaculada Esperanza Remedios Consuelo Amparo Begoña""",
        "M": """Javier Alejandro Jesus Pedro Miguel Rafael Fernando Enrique Ramon Alfonso Ignacio
        Joaquin Santiago Vicente Emilio Andres Alberto Diego Raul Sergio""",
    },
    "italie": {
        "sur": """Rossi Russo Ferrari Esposito Bianchi Romano Colombo Ricci Marino Greco Bruno
        Gallo Conti DeLuca Costa Giordano Mancini Rizzo Lombardi Moretti Barbieri Fontana
        Santoro Mariani Rinaldi Caruso Ferrara Galli Martini Leone Longo Gentile Martinelli
        Vitale Lombardo Serra Coppola DeSantis Marchetti Parisi Villa Conte Ferraro Fabbri""",
        "F": """Giulia Francesca Chiara Federica Alessandra Giovanna Silvia Valentina 
        Martina Rosaria Antonella Graziella Paola Roberta Ornella Donatella Gabriella""",
        "M": """Giuseppe Giovanni Francesco Antonio Salvatore Luigi Vincenzo Pietro Stefano
        Alessandro Andrea Giorgio Lorenzo Matteo Luca Roberto Angelo Domenico Carlo Sergio""",
    },
    "turquie": {
        "sur": """Yilmaz Kaya Demir Sahin Celik Yildiz Yildirim Ozturk Aydin Ozdemir Arslan
        Dogan Kilic Aslan Cetin Kara Koc Kurt Ozkan Simsek Polat Ozcan Korkmaz Cakir Erdogan
        Yavuz Can Acar Sen Aktas Guler Yalcin Gunes Bozkurt Bulut Keskin Unal Turan Gul""",
        "F": """Ayse Fatma Emine Hatice Zeynep Elif Esra Merve Busra Tugba Hacer Sevgi Gulsen Ozlem
        Derya Yasemin Sibel Songul""",
        "M": """Mehmet Mustafa Ahmet Huseyin Hasan Ibrahim Ismail Osman Yusuf Murat Omer Ramazan
        Halil Suleyman Abdullah Recep Kemal Emre Burak Serkan""",
    },
    "afrique-ouest": {
        "sur": """Traore Diallo Diarra Kone Camara Coulibaly Keita Sow Ba Ndiaye Diop Fall Sy
        Diouf Gueye Faye Cisse Toure Sangare Sylla Konate Dembele Doumbia Bamba Ouattara
        Kouassi Kouame Yao Kouadio Konan Soumare Niang Mbaye Thiam Sarr Seck Kante Barry
        Bah Balde Sissoko Fofana Dramé Tounkara""",
        "F": """Aminata Fatoumata Mariama Aissatou Kadiatou Oumou Awa Adama Djeneba Assetou
        Ramatoulaye Bintou Mariam Hawa Coumba Ndeye Astou Rokhaya Khady Affoue""",
        "M": """Mamadou Moussa Ibrahima Ousmane Amadou Boubacar Seydou Souleymane Cheikh Abdoulaye
        Lamine Modibo Bakary Samba Oumar Aliou Babacar Mamadi Sekou Yaya""",
    },
    "afrique-centrale": {
        "sur": """Mbemba Nkounkou Makosso Loemba Mabiala Moukoko Ngoma Mbala Kabamba Kalonji
        Mukendi Tshibangu Ilunga Kabila Mbuyi Nzuzi Lumbala Mavoungou Nsimba Mpika Ekambi
        Etoundi Mbarga Nkoulou Fotso Tchoupo Kamdem Ngassa Tchinda Nana Ondoa Essomba""",
        "F": """ Merveille Prisca Divine  Ornella Gloire Exaucee Patience Chancelle
        Fortunee Bienvenue   Plamedie Dorcas""",
        "M": """   Destin Christ Exauce Dieudonne Fiston Glody Merveil
             """,
    },
    "vietnam": {
        "sur": """Nguyen Tran Pham Hoang Huynh Phan Truong Dang Bui Ngo Duong Dinh Trinh Doan
        Luong Vuong Mai Cao Lam Quach Thai Vo Ly""",
        "F": """Thi Lan Huong Linh Trang Thuy Hoa Hanh Hang Phuong Ngoc Thanh Hien Nhung Yen""",
        "M": """Van Minh Tuan Hung Dung Quang Duc Hai Thang Long Nam Khanh Huy Phuc Trung""",
    },
    "chine": {
        "sur": """Wang Zhang Chen Yang Huang Zhao Zhou Zhu Xu Sun Guo Lin He Gao Luo Zheng Liang
        Xie Song Tang Han Feng Deng Cao Peng Zeng Xiao Tian Dong Pan Yuan Jiang Cai Jin""",
        "F": """Xiaoling Mei Jing Yan Ying Fang Li Na Xiu Hui Lian Qing Xin Yun Jia Ling""",
        "M": """Wei Jian Jun Ming Qiang Lei Tao Hao Bo Gang Hui Yong Feng Chao Xiang Hong""",
    },
    "roumanie": {
        "sur": """Popescu Ionescu Popa Pop Radu Dumitru Stoica Stan Gheorghe Matei Ciobanu
        Rusu Munteanu  Moldovan Lazar Florea Dinu Tudor Serban Barbu Nistor Ene
        Dobre Toma  Oprea Stanciu Diaconu Cristea""",
        "F": """Ioana Elena Andreea Alexandra Mihaela Florentina Georgiana Gabriela Roxana
        Loredana Nicoleta Daniela Cristina Raluca Oana Irina""",
        "M": """Ion Gheorghe Vasile  Nicolae Mihai Andrei Florin Cristian Bogdan Marius
        Ionut Adrian Sorin Catalin Dorin""",
    },
    "pologne": {
        "sur": """Nowak Kowalski Wisniewski Wojcik Kowalczyk Kaminski Lewandowski Zielinski
        Szymanski Wozniak Dabrowski Kozlowski Jankowski Mazur Kwiatkowski Krawczyk Piotrowski
        Grabowski Nowakowski Pawlowski Michalski Nowicki Adamczyk Dudek Zajac Wieczorek
        Jablonski Krol Majewski Olszewski""",
        "F": """Agnieszka Katarzyna Malgorzata Joanna Ewa Magdalena Monika Dorota Beata Grazyna
        Krystyna Jadwiga Halina Danuta Zofia Kinga""",
        "M": """Krzysztof Piotr Andrzej Tomasz Pawel Jan Michal Marcin Grzegorz Jakub Lukasz
        Wojciech Zbigniew Stanislaw Janusz Tadeusz""",
    },
    "russie": {
        "sur": """Ivanov Smirnov Kuznetsov Popov Vasiliev Petrov Sokolov Mikhailov Novikov Fedorov
        Morozov Volkov Alekseev Lebedev Semenov Egorov Pavlov Kozlov Stepanov Nikolaev Orlov
        Andreev Makarov Zakharov Shevchenko Kovalenko Bondarenko Tkachenko Kravchenko Melnyk
        Boyko Kovalchuk Oliynyk Shevchuk Polishchuk""",
        "F": """Olga Tatiana Svetlana Natalia Irina Ekaterina Anastasia Yulia Oksana Ludmila
        Galina Valentina Larisa Nadezhda Ksenia Daria""",
        "M": """Sergei Dmitri Alexei Vladimir Nikolai Mikhail Andrei Ivan Yuri Oleg Pavel
        Viktor Igor Bogdan Taras Vitali""",
    },
    "anglo": {
        "sur": """Smith Jones Williams Taylor Brown Davies Evans Wilson Johnson Roberts Robinson
        Wright Thompson Walker White Edwards Hughes Green Hall Lewis Harris Clarke 
        Jackson Wood Turner Cooper Hill Ward Morris Moore Clark King Harrison Baker""",
        "F": """Jennifer Jessica Ashley Emily Hannah Megan Rachel Rebecca Samantha Lauren Kimberly
        Stephanie Amanda Heather Nicole Brittany""",
        "M": """James John   William      Christopher
        Matthew  Andrew Joshua Brian""",
    },
}

# names shared by several origins stay with the first one listed above
ORIGIN_OF = {"sur": {}, "F": {}, "M": {}}
for _origin, _lists in ORIGINS.items():
    for _kind, _names in _lists.items():
        for _name in _names.split():
            ORIGIN_OF[_kind].setdefault(_name, _origin)
